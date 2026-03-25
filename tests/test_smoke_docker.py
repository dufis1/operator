"""
SMOKE TEST — Docker adapter end-to-end

Purpose: Verify the full pipeline works inside the Docker container:
  1. Container starts, PulseAudio initialises, headless Chrome joins the test meeting
  2. A pre-recorded "operator, say the word hello" clip is injected as meeting audio
  3. The container produces audio output (TTS response) on the MeetingOutput sink within 30s

Pass condition: audio bytes are captured from MeetingOutput.monitor after the clip is injected.

Required env vars (set locally or via GitHub Secrets in CI):
    OPENAI_API_KEY          OpenAI key for the LLM
    ELEVENLABS_API_KEY      ElevenLabs key for TTS
    SMOKE_TEST_MEETING_URL  Persistent Google Meet link for the Operator test account

How to run locally:
    SMOKE_TEST_MEETING_URL=https://meet.google.com/abc-defg-hij \\
    python tests/test_smoke_docker.py

What it does:
    1. Checks Docker daemon is running and required env vars are set
    2. Builds the production image (docker/Dockerfile) tagged 'operator-smoke'
    3. Starts the container in the background
    4. Polls docker logs until "in meeting" appears (up to 90s) — confirms Chrome joined
    5. Plays assets/smoke_test_prompt.mp3 into MeetingInput PulseAudio sink via docker exec
       This simulates a meeting participant saying "operator, say the word hello"
    6. Captures audio from MeetingOutput.monitor (where TTS plays) for up to 30s
    7. Asserts non-trivial audio was received
    8. Stops and removes the container

Audio injection detail:
    paplay inside the container plays the clip into the MeetingInput sink.
    parec reads MeetingInput.monitor, which is what AudioProcessor receives.
    The Operator hears the clip exactly as it would hear a real meeting participant.

Audio capture detail:
    A second docker exec runs parec on MeetingOutput.monitor.
    Any TTS output (mpv → MeetingOutput) will appear on this monitor.
"""

import os
import subprocess
import sys
import threading
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE_NAME = "operator-smoke"
CONTAINER_NAME = "operator-smoke-run"
PROMPT_CLIP = os.path.join(PROJECT_ROOT, "assets", "smoke_test_prompt.mp3")

# How long to wait for Chrome to join the meeting before giving up
JOIN_TIMEOUT_S = 90
# How long to capture TTS output after injecting the prompt
RESPONSE_TIMEOUT_S = 30
# Minimum bytes from MeetingOutput.monitor to count as "audio received"
MIN_AUDIO_BYTES = 4096


def log(msg: str):
    print(f"  → {msg}", flush=True)


def result(passed: bool, detail: str):
    print(flush=True)
    print("=" * 60, flush=True)
    print("RESULT: PASS ✓" if passed else "RESULT: FAIL ✗", flush=True)
    print(detail, flush=True)
    print("=" * 60, flush=True)
    sys.exit(0 if passed else 1)


def _stop_container():
    subprocess.run(
        ["docker", "stop", CONTAINER_NAME],
        capture_output=True,
    )
    subprocess.run(
        ["docker", "rm", "-f", CONTAINER_NAME],
        capture_output=True,
    )


def main():
    print(flush=True)
    print("SMOKE TEST — Docker adapter end-to-end", flush=True)
    print(flush=True)

    # ── 1. Pre-flight checks ──────────────────────────────────────────────

    log("Checking Docker daemon...")
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        result(False, "Docker daemon is not running. Start Docker Desktop and retry.")

    missing = [
        k for k in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "SMOKE_TEST_MEETING_URL")
        if not os.environ.get(k)
    ]
    if missing:
        result(False, f"Missing required env vars: {', '.join(missing)}")

    if not os.path.exists(PROMPT_CLIP):
        result(False, f"Prompt clip not found: {PROMPT_CLIP}\nRun: say -o /tmp/p.aiff 'operator, say the word hello' && ffmpeg -i /tmp/p.aiff {PROMPT_CLIP}")

    log("Pre-flight checks passed.")

    # ── 2. Build production image ─────────────────────────────────────────

    log(f"Building image '{IMAGE_NAME}' (cached layers make this fast after first run)...")
    build = subprocess.run(
        ["docker", "build", "-f", "docker/Dockerfile", "-t", IMAGE_NAME, "."],
        cwd=PROJECT_ROOT,
    )
    if build.returncode != 0:
        result(False, "Docker build failed. Check the output above.")

    log("Image built.")

    # ── 3. Start the container ────────────────────────────────────────────

    _stop_container()  # clean up any leftover run from a previous attempt

    log("Starting container...")
    run = subprocess.run(
        [
            "docker", "run",
            "--name", CONTAINER_NAME,
            "--detach",
            "-e", f"OPENAI_API_KEY={os.environ['OPENAI_API_KEY']}",
            "-e", f"ELEVENLABS_API_KEY={os.environ['ELEVENLABS_API_KEY']}",
            "-e", f"MEETING_URL={os.environ['SMOKE_TEST_MEETING_URL']}",
            IMAGE_NAME,
        ],
        capture_output=True,
        text=True,
    )
    if run.returncode != 0:
        result(False, f"docker run failed:\n{run.stderr}")

    container_id = run.stdout.strip()
    log(f"Container started: {container_id[:12]}")

    try:
        # ── 4. Wait for "in meeting" in logs ──────────────────────────────

        log(f"Waiting up to {JOIN_TIMEOUT_S}s for Chrome to join the meeting...")
        deadline = time.time() + JOIN_TIMEOUT_S
        joined = False
        while time.time() < deadline:
            logs = subprocess.run(
                ["docker", "logs", CONTAINER_NAME],
                capture_output=True,
                text=True,
            )
            combined = logs.stdout + logs.stderr
            if "in meeting" in combined.lower():
                joined = True
                break
            time.sleep(3)

        if not joined:
            # Print last 30 lines of logs for diagnosis
            logs = subprocess.run(
                ["docker", "logs", "--tail", "30", CONTAINER_NAME],
                capture_output=True,
                text=True,
            )
            print(logs.stdout[-2000:])
            print(logs.stderr[-2000:])
            result(False, f"Container did not join the meeting within {JOIN_TIMEOUT_S}s.")

        log("Chrome joined the meeting.")

        # ── 5. Inject prompt clip into MeetingInput ───────────────────────

        # Copy the prompt clip into the container, then play it into MeetingInput.
        log("Copying prompt clip into container...")
        cp = subprocess.run(
            ["docker", "cp", PROMPT_CLIP, f"{CONTAINER_NAME}:/tmp/smoke_test_prompt.mp3"],
            capture_output=True,
        )
        if cp.returncode != 0:
            result(False, f"docker cp failed: {cp.stderr.decode()}")

        log("Injecting prompt clip into MeetingInput PulseAudio sink...")
        # mpv plays into MeetingInput — AudioProcessor reads MeetingInput.monitor.
        inject = subprocess.Popen(
            [
                "docker", "exec", CONTAINER_NAME,
                "mpv",
                "--no-terminal",
                "--audio-device=pulse/MeetingInput",
                "/tmp/smoke_test_prompt.mp3",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Don't wait — start capturing output immediately so we don't miss it.

        # ── 6. Capture audio from MeetingOutput.monitor ───────────────────

        log(f"Capturing TTS output from MeetingOutput for up to {RESPONSE_TIMEOUT_S}s...")
        captured_bytes = []
        capture_done = threading.Event()

        def _capture():
            proc = subprocess.Popen(
                [
                    "docker", "exec", CONTAINER_NAME,
                    "parec",
                    "--device=MeetingOutput.monitor",
                    "--format=float32le",
                    "--rate=16000",
                    "--channels=1",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            deadline_inner = time.time() + RESPONSE_TIMEOUT_S
            while time.time() < deadline_inner and not capture_done.is_set():
                chunk = proc.stdout.read(4096)
                if chunk:
                    captured_bytes.append(chunk)
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()

        capture_thread = threading.Thread(target=_capture, daemon=True)
        capture_thread.start()
        capture_thread.join(timeout=RESPONSE_TIMEOUT_S + 5)
        capture_done.set()

        inject.wait(timeout=10)

        total_bytes = sum(len(b) for b in captured_bytes)
        log(f"Captured {total_bytes} bytes from MeetingOutput.monitor.")

        # ── 7. Assert ─────────────────────────────────────────────────────

        if total_bytes >= MIN_AUDIO_BYTES:
            result(
                True,
                f"Operator heard the prompt and produced audio output.\n"
                f"Bytes captured from MeetingOutput: {total_bytes}\n\n"
                "SMOKE TEST PASSES.\n"
                "Next: end-of-phase commit → Phase 4.",
            )
        else:
            # Print recent logs for diagnosis
            logs = subprocess.run(
                ["docker", "logs", "--tail", "40", CONTAINER_NAME],
                capture_output=True,
                text=True,
            )
            print(logs.stdout[-3000:])
            print(logs.stderr[-3000:])
            result(
                False,
                f"No audio response detected within {RESPONSE_TIMEOUT_S}s.\n"
                f"Bytes captured: {total_bytes} (threshold: {MIN_AUDIO_BYTES}).\n"
                "Check logs above — wake phrase may not have triggered, or LLM/TTS may have failed.",
            )

    finally:
        log("Stopping container...")
        _stop_container()
        log("Container stopped and removed.")


if __name__ == "__main__":
    main()
