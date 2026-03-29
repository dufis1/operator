"""
Operator — AI Meeting Participant
Cross-platform entry point. Auto-detects OS and dispatches to the right adapter.

Usage:
    python __main__.py              # macOS: opens menu bar app (CalDAV auto-join)
    python __main__.py <meet-url>   # Linux: joins a specific meeting
    python .                        # same as above from the repo root

Note: `python -m operator` conflicts with Python's built-in `operator` module.
      It will work correctly once the package is installed via pyproject.toml (Step 8.1).
"""
import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="operator",
        description="Operator — AI meeting participant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "macOS: meeting URL is not required — Operator monitors your calendar\n"
            "       via CalDAV and joins automatically.\n\n"
            "Linux: meeting URL is required. Set DISPLAY and run\n"
            "       scripts/linux_setup.sh before starting."
        ),
    )
    parser.add_argument(
        "meeting_url",
        nargs="?",
        metavar="MEET_URL",
        help="Google Meet URL to join (Linux only)",
    )
    args = parser.parse_args()

    if sys.platform == "darwin":
        if args.meeting_url:
            _run_macos_headless(args.meeting_url)
        else:
            _run_macos()
    else:
        _run_linux(args.meeting_url)


def _run_macos():
    """Launch the macOS menu bar app."""
    from app import OperatorApp
    OperatorApp().run()


def _run_macos_headless(meeting_url):
    """Join a specific meeting directly on macOS, no menu bar."""
    import logging
    import signal

    logging.basicConfig(
        filename="/tmp/operator.log",
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    # Also print to stderr so the terminal shows progress
    logging.getLogger().addHandler(logging.StreamHandler(sys.stderr))
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("elevenlabs").setLevel(logging.WARNING)

    log = logging.getLogger("operator")

    from connectors.macos_adapter import MacOSAdapter
    from pipeline.runner import AgentRunner

    BLACKHOLE_DEVICE = "coreaudio/BlackHole2ch_UID"

    log.info(f"Starting Operator (macOS headless) — joining {meeting_url}")
    connector = MacOSAdapter()
    runner = AgentRunner(
        connector=connector,
        tts_output_device=BLACKHOLE_DEVICE,
    )

    def _shutdown(signum=None, frame=None):
        if signum:
            log.info(f"Received signal {signum} — shutting down")
        runner.stop()
        connector.leave()

    signal.signal(signal.SIGTERM, _shutdown)

    try:
        runner.run(meeting_url)
    except KeyboardInterrupt:
        log.info("Interrupted — leaving meeting")
    finally:
        _shutdown()


def _run_linux(meeting_url):
    """Run preflight checks then start the agent on Linux."""
    import logging
    import os
    import signal
    import subprocess

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    log = logging.getLogger("operator")

    # Resolve meeting URL
    if not meeting_url:
        meeting_url = os.environ.get("MEETING_URL")
    if not meeting_url:
        print("\n❌ A meeting URL is required on Linux:\n")
        print("   python __main__.py <meet-url>")
        print("   MEETING_URL=<url> python __main__.py\n")
        sys.exit(1)

    # Check $DISPLAY (required for headless Chrome audio rendering)
    display = os.environ.get("DISPLAY")
    if not display:
        log.error(
            "DISPLAY is not set. Start Xvfb first:\n"
            "  Xvfb :99 -screen 0 1920x1080x24 &\n"
            "  export DISPLAY=:99"
        )
        print("\n❌ DISPLAY is not set — start Xvfb first:\n")
        print("   Xvfb :99 -screen 0 1920x1080x24 &")
        print("   export DISPLAY=:99\n")
        sys.exit(1)
    log.info(f"DISPLAY={display}")

    # Check PulseAudio virtual sinks
    try:
        result = subprocess.run(
            ["pactl", "list", "short", "sinks"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        log.error("pactl not found — is PulseAudio installed?")
        print("\n❌ pactl not found — install PulseAudio:\n")
        print("   apt install pulseaudio\n")
        sys.exit(1)

    missing = [s for s in ("MeetingOutput", "MeetingInput") if s not in result.stdout]
    if missing:
        log.error(
            f"Missing PulseAudio sinks: {missing}\n"
            "Run scripts/linux_setup.sh first:\n"
            "  bash scripts/linux_setup.sh"
        )
        print("\n❌ Missing PulseAudio sinks — run the setup script:\n")
        print("   bash scripts/linux_setup.sh\n")
        sys.exit(1)
    log.info("PulseAudio sinks: MeetingOutput and MeetingInput found")

    # Start the agent
    from connectors.linux_adapter import LinuxAdapter
    from pipeline.runner import AgentRunner

    log.info(f"Starting Operator (Linux) — joining {meeting_url}")
    connector = LinuxAdapter()
    runner = AgentRunner(
        connector=connector,
        tts_output_device="pulse/MeetingOutput",
    )

    def _shutdown(signum=None, frame=None):
        if signum:
            log.info(f"Received signal {signum} — shutting down")
        runner.stop()
        connector.leave()

    signal.signal(signal.SIGTERM, _shutdown)

    try:
        runner.run(meeting_url)
    except KeyboardInterrupt:
        log.info("Interrupted — leaving meeting")
    finally:
        _shutdown()


if __name__ == "__main__":
    main()
