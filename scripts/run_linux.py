"""
run_linux.py — Linux local entry point for Operator.

Usage:
    python scripts/run_linux.py <meet-url>
    python scripts/run_linux.py  # reads MEETING_URL from environment

Prerequisites:
    1. PulseAudio virtual devices must be set up:
           bash scripts/linux_setup.sh
    2. Xvfb must be running and DISPLAY must be set:
           Xvfb :99 -screen 0 1920x1080x24 &
           export DISPLAY=:99
    3. API keys must be in .env (OPENAI_API_KEY, ELEVENLABS_API_KEY).
"""
import logging
import os
import subprocess
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger("run_linux")


def _check_display():
    display = os.environ.get("DISPLAY")
    if not display:
        log.error(
            "DISPLAY is not set. Start Xvfb first:\n"
            "  Xvfb :99 -screen 0 1920x1080x24 &\n"
            "  export DISPLAY=:99"
        )
        sys.exit(1)
    log.info(f"DISPLAY={display}")


def _check_pulse_sinks():
    """Verify that the required PulseAudio virtual sinks exist."""
    try:
        result = subprocess.run(
            ["pactl", "list", "short", "sinks"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        log.error("pactl not found — is PulseAudio installed?")
        sys.exit(1)

    sinks = result.stdout
    missing = [s for s in ("MeetingOutput", "MeetingInput") if s not in sinks]
    if missing:
        log.error(
            f"Missing PulseAudio sinks: {missing}\n"
            "Run scripts/linux_setup.sh first:\n"
            "  bash scripts/linux_setup.sh"
        )
        sys.exit(1)
    log.info("PulseAudio sinks: MeetingOutput and MeetingInput found")


def main():
    # Resolve meeting URL
    if len(sys.argv) > 1:
        meeting_url = sys.argv[1]
    else:
        meeting_url = os.environ.get("MEETING_URL")

    if not meeting_url:
        print("Usage: python scripts/run_linux.py <meet-url>")
        print("       MEETING_URL=<url> python scripts/run_linux.py")
        sys.exit(1)

    _check_display()
    _check_pulse_sinks()

    # Import after env checks so misconfigured environments fail fast with clear messages
    from connectors.linux_adapter import LinuxAdapter
    from pipeline.runner import AgentRunner

    log.info(f"Starting Operator (Linux) — joining {meeting_url}")

    connector = LinuxAdapter()
    runner = AgentRunner(
        connector=connector,
        tts_output_device="pulse/MeetingOutput",
    )

    try:
        runner.run(meeting_url)
    except KeyboardInterrupt:
        log.info("Interrupted — leaving meeting")
        runner.stop()
        connector.leave()


if __name__ == "__main__":
    main()
