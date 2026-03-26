"""
PROBE B.2 — Docker container: PulseAudio + Whisper accuracy

Purpose: Verify that the Docker environment for the cloud adapter can:
  1. Start PulseAudio (the virtual audio device used to capture meeting audio)
  2. Run faster-whisper with acceptable accuracy (avg WER ≤ 20%)

Pass condition: both checks pass.
  - WER baseline from local benchmark: ~10%. Threshold is 2× to allow headroom.
  - PulseAudio must start cleanly — if it doesn't, the Docker audio path is broken.

How to run:
    python tests/probe_b2_whisper_docker.py

What it does:
    1. Checks Docker daemon is running
    2. Builds cloud/docker/Dockerfile.probe_b2 as image 'operator-probe-b2'
    3. Runs the container, which transcribes benchmark_clips/ inside Docker
    4. Parses the JSON result and prints PASS or FAIL

Note: First run downloads the Whisper base model (~150 MB) inside the container.
Subsequent runs are fast because Docker caches the pip install layer.
"""

import json
import os
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE_NAME = "operator-probe-b2"
WER_THRESHOLD = 0.20


def log(msg: str):
    print(f"  → {msg}")


def result(passed: bool, detail: str):
    print()
    print("=" * 60)
    print("RESULT: PASS ✓" if passed else "RESULT: FAIL ✗")
    print(detail)
    print("=" * 60)
    sys.exit(0 if passed else 1)


def main():
    print()
    print("PROBE B.2 — Docker: PulseAudio + Whisper accuracy")
    print()

    # Check Docker daemon
    log("Checking Docker daemon...")
    check = subprocess.run(["docker", "info"], capture_output=True)
    if check.returncode != 0:
        result(False, "Docker daemon is not running. Start Docker Desktop and retry.")

    log("Docker daemon is running.")

    # Build the image
    log(f"Building image '{IMAGE_NAME}' (first run installs deps — may take ~3 min)...")
    build = subprocess.run(
        ["docker", "build", "-f", "cloud/docker/Dockerfile.probe_b2", "-t", IMAGE_NAME, "."],
        cwd=PROJECT_ROOT,
    )
    if build.returncode != 0:
        result(False, "Docker build failed. Check the output above for errors.")

    log("Image built.")

    # Run the container
    log("Running benchmark inside container...")
    proc = subprocess.run(
        ["docker", "run", "--rm", IMAGE_NAME],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )

    # Exit codes 0 (pass) and 1 (fail from whisper_bench.py) are both expected.
    # Anything else means the container itself crashed.
    if proc.returncode not in (0, 1):
        print("--- STDOUT ---")
        print(proc.stdout[:3000])
        print("--- STDERR ---")
        print(proc.stderr[:3000])
        result(False, f"Container crashed with exit code {proc.returncode}.")

    # Parse JSON result from whisper_bench.py
    try:
        bench = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print("--- STDOUT ---")
        print(proc.stdout[:3000])
        print("--- STDERR ---")
        print(proc.stderr[:3000])
        result(False, "Could not parse container output. Check logs above.")

    # Print per-clip results
    print()
    print(f"  {'Clip':<15} {'WER':>8}  Transcript")
    print("  " + "─" * 72)
    for clip_name, clip_data in bench.get("clips", {}).items():
        wer_pct = f"{clip_data['wer']:.1%}"
        transcript = clip_data.get("transcript", "")[:60]
        print(f"  {clip_name:<15} {wer_pct:>8}  {transcript}")

    avg_wer = bench.get("avg_wer")
    pa_ok = bench.get("pulseaudio_ok", False)

    print()
    print(f"  PulseAudio started: {'YES' if pa_ok else 'NO'}")
    if avg_wer is not None:
        print(f"  Average WER:        {avg_wer:.1%}  (threshold: {WER_THRESHOLD:.0%})")
    else:
        print("  Average WER:        n/a")

    passed = bench.get("pass", False)

    if passed:
        result(
            True,
            f"PulseAudio: running.\n"
            f"Whisper avg WER: {avg_wer:.1%} (≤ {WER_THRESHOLD:.0%} threshold).\n\n"
            "PROBE B PASSES.\n"
            "Both Probe A and Probe B pass → proceed as planned.\n"
            "Next step: Phase 0 — codebase cleanup.",
        )
    else:
        failures = []
        if not pa_ok:
            failures.append("PulseAudio failed to start inside the container.")
        if avg_wer is not None and avg_wer > WER_THRESHOLD:
            failures.append(
                f"Whisper avg WER {avg_wer:.1%} exceeds {WER_THRESHOLD:.0%} threshold."
            )
        result(False, "\n".join(failures) or "Unknown failure — check logs above.")


if __name__ == "__main__":
    main()
