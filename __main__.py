"""
Operator — AI Meeting Participant
Cross-platform entry point. Auto-detects OS and dispatches to the right adapter.

Usage:
    python __main__.py              # macOS: opens menu bar app (calendar auto-join)
    python __main__.py <meet-url>   # Linux: joins a specific meeting
    python .                        # same as above from the repo root

Note: `python -m operator` conflicts with Python's built-in `operator` module.
      It will work correctly once the package is installed via pyproject.toml (Step 8.1).
"""
import argparse
import os
import subprocess
import sys


# ── Prevent Ctrl+C from killing child processes ────────────────────
# Playwright's Node.js driver and Chrome are child processes in our
# terminal's foreground process group.  When the user presses Ctrl+C,
# the terminal sends SIGINT to the whole group — killing Chrome
# abruptly and leaving it in the meeting for ~60s until Meet's
# heartbeat times out.
#
# Fix: put every child in its own session (setsid) so SIGINT only
# reaches our Python process.  We then close Chrome cleanly via
# Playwright, and Meet sees an immediate disconnect.
_OriginalPopenInit = subprocess.Popen.__init__


def _detached_popen_init(self, *args, **kwargs):
    kwargs.setdefault("start_new_session", True)
    _OriginalPopenInit(self, *args, **kwargs)


subprocess.Popen.__init__ = _detached_popen_init


def _kill_orphaned_children():
    """Last-resort cleanup: kill any child processes that survived graceful shutdown.

    Uses pgrep to find direct children of this process, sends SIGTERM,
    waits briefly, then SIGKILL any survivors. Works on macOS and Linux.
    No recursion needed — children use start_new_session=True so killing
    the direct child is sufficient for its own process group.
    """
    import signal as _sig
    import subprocess as _sp
    import time as _time

    pid = os.getpid()
    try:
        result = _sp.run(
            ["pgrep", "-P", str(pid)],
            capture_output=True, text=True, timeout=3,
            start_new_session=False,
        )
    except Exception:
        return

    child_pids = [int(p) for p in result.stdout.strip().split("\n") if p.strip()]
    if not child_pids:
        return

    import logging
    log = logging.getLogger("operator")
    log.warning(f"Safety net: killing {len(child_pids)} orphaned child process(es): {child_pids}")

    for cpid in child_pids:
        try:
            os.kill(cpid, _sig.SIGTERM)
        except ProcessLookupError:
            pass

    _time.sleep(0.5)

    for cpid in child_pids:
        try:
            os.kill(cpid, 0)  # check if still alive
            os.kill(cpid, _sig.SIGKILL)
            log.warning(f"Safety net: SIGKILL sent to pid {cpid}")
        except ProcessLookupError:
            pass


def _check_mcp() -> int:
    """Validate MCP config: start each server, list tools, print summary, exit.

    Exit code 0 if every configured server loaded, 1 otherwise. Intended for
    local debugging and CI — avoids having to join a real meeting to test DIY
    MCP server configs.
    """
    import logging
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    import config
    from pipeline.mcp_client import MCPClient

    if not config.MCP_SERVERS:
        print("No mcp_servers configured in config.yaml.")
        return 0

    print(f"Starting {len(config.MCP_SERVERS)} MCP server(s)...")
    client = MCPClient()
    try:
        tool_names = client.connect_all()
    finally:
        pass

    print()
    for name in config.MCP_SERVERS:
        if name in client.failed_servers:
            print(f"  ✗ {name}")
            print(f"        {client.failed_servers[name]}")
        else:
            count = sum(1 for t in tool_names if t.startswith(f"{name}__"))
            print(f"  ✓ {name}  ({count} tools)")

    print()
    loaded = len(config.MCP_SERVERS) - len(client.failed_servers)
    print(f"Summary: {loaded} of {len(config.MCP_SERVERS)} servers loaded, {len(tool_names)} tools available.")

    client.shutdown()
    return 0 if not client.failed_servers else 1


def _print_mcp_startup_banner(mcp):
    """Print a one-line MCP status banner to stderr so users see broken
    servers without needing to tail /tmp/operator.log.

    Called right before the meeting join. Non-blocking — a failed server
    does not abort startup, it just warns. Users who want a hard gate
    should run `--check-mcp` before launching.
    """
    import config
    import sys as _sys
    if not config.MCP_SERVERS:
        return
    parts = []
    for name in config.MCP_SERVERS:
        if name in mcp.failed_servers:
            parts.append(f"{name} ✗")
        else:
            parts.append(f"{name} ✓")
    loaded = len(config.MCP_SERVERS) - len(mcp.failed_servers)
    total = len(config.MCP_SERVERS)
    suffix = "" if not mcp.failed_servers else " — run --check-mcp for details"
    print(f"MCP: {loaded}/{total} servers loaded ({', '.join(parts)}){suffix}", file=_sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        prog="operator",
        description="Operator — AI meeting participant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "macOS: meeting URL is not required — Operator monitors your calendar\n"
            "       via Google Calendar and joins automatically.\n\n"
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
    parser.add_argument(
        "--force",
        action="store_true",
        help="Kill any existing Operator session and start a new one",
    )
    parser.add_argument(
        "--chat",
        action="store_true",
        help="Run in chat-only mode (no voice pipeline)",
    )
    parser.add_argument(
        "--check-mcp",
        action="store_true",
        help="Validate MCP server config: start each server, list tools, then exit. No meeting join.",
    )
    args = parser.parse_args()

    if args.check_mcp:
        sys.exit(_check_mcp())

    if sys.platform == "darwin":
        _run_macos_terminal(args.meeting_url, force=args.force, chat_mode=args.chat)
    else:
        _run_linux(args.meeting_url, force=args.force, chat_mode=args.chat)


def _run_macos():
    """Launch the macOS menu bar app (Operator.app only)."""
    from app import OperatorApp
    OperatorApp().run()


def _run_macos_terminal(meeting_url=None, force=False, chat_mode=False):
    """Run from terminal on macOS — calendar polling or direct URL.

    Keeps the main thread in Python code so SIGINT (Ctrl+C) is handled
    reliably.  The rumps menu bar is only used when launched as Operator.app.
    """
    import logging
    import os
    import queue
    import signal

    logging.basicConfig(
        filename="/tmp/operator.log",
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    # Also print to stderr so the terminal shows progress
    _console = logging.StreamHandler(sys.stderr)
    _console.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d %(message)s", datefmt="%H:%M:%S"))
    logging.getLogger().addHandler(_console)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("elevenlabs").setLevel(logging.WARNING)

    log = logging.getLogger("operator")

    import config

    BLACKHOLE_DEVICE = "coreaudio/BlackHole2ch_UID"

    # Resolve chat mode from --chat flag or config
    use_chat = chat_mode or config.INTERACTION_MODE == "chat"

    mcp = None  # only used in chat mode

    if use_chat:
        import time as _time
        import threading as _threading
        t_chat_start = _time.monotonic()
        from openai import OpenAI
        from connectors.macos_adapter import MacOSAdapter
        from pipeline.chat_runner import ChatRunner
        from pipeline.llm import LLMClient
        connector = MacOSAdapter(force=force)
        llm = LLMClient(OpenAI(api_key=config.OPENAI_API_KEY), mode="chat")

        # Start MCP connection in background while browser joins
        _mcp_result = {"client": None}
        def _connect_mcp():
            t_mcp = _time.monotonic()
            from pipeline.mcp_client import MCPClient
            client = MCPClient()
            try:
                tool_names = client.connect_all()
                log.info(f"TIMING mcp_connect={_time.monotonic() - t_mcp:.1f}s ({len(tool_names)} tools)")
                _mcp_result["client"] = client
            except Exception as e:
                log.error(f"MCP client startup failed: {e}")

        if config.MCP_SERVERS:
            mcp_thread = _threading.Thread(target=_connect_mcp, daemon=True)
            mcp_thread.start()
        else:
            mcp_thread = None

        # Start browser join (runs in its own thread inside connector)
        connector.join(meeting_url)

        # Wait for MCP to finish (overlaps with browser join)
        if mcp_thread:
            mcp_thread.join()
            mcp = _mcp_result["client"]
            if mcp:
                llm.inject_mcp_hints(config.MCP_SERVERS)
                loaded = [n for n in config.MCP_SERVERS if n not in mcp.failed_servers]
                llm.inject_mcp_status(loaded, mcp.failed_servers)
                _print_mcp_startup_banner(mcp)
                gh_login = mcp.resolve_github_user()
                if gh_login:
                    llm.inject_github_user(gh_login)

        log.info(f"TIMING chat_setup={_time.monotonic() - t_chat_start:.1f}s")
        runner = ChatRunner(connector, llm, mcp_client=mcp)
    else:
        from pipeline.runner import AgentRunner
        connector_type = config.CONNECTOR_TYPE

        # Resolve "auto" → default to meet-captions on macOS
        if connector_type == "auto":
            connector_type = "meet-captions"

        if connector_type == "meet-captions":
            from connectors.captions_adapter import CaptionsAdapter
            connector = CaptionsAdapter(force=force)
        elif connector_type == "audio":
            from connectors.macos_adapter import MacOSAdapter
            connector = MacOSAdapter(force=force)
        else:
            log.error(f"Unknown connector type: {connector_type}")
            sys.exit(1)

        runner = AgentRunner(
            connector=connector,
            tts_output_device=BLACKHOLE_DEVICE,
        )

    poller = None
    _shutdown_called = False

    def _shutdown(signum=None, frame=None):
        nonlocal _shutdown_called
        if _shutdown_called:
            return
        _shutdown_called = True
        reason_file = os.path.join(config.BROWSER_PROFILE_DIR, ".operator.kill_reason")
        try:
            with open(reason_file) as _f:
                reason = _f.read().strip()
            os.remove(reason_file)
            print(f"\n⚠️  {reason}\n")
            log.info(reason)
        except FileNotFoundError:
            if signum:
                log.info(f"Received signal {signum} — shutting down")
        runner.stop()
        if mcp:
            mcp.shutdown()
        if poller:
            poller.stop()
        connector.leave()
        _kill_orphaned_children()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        if meeting_url:
            log.info(f"Starting Operator — joining {meeting_url}")
            runner.run(meeting_url)
            # If run() returned without an explicit stop, the exit was unexpected
            if not runner._stop_event.is_set():
                print(f"\n   Restart with: python __main__.py {'--chat ' if use_chat else ''}{meeting_url}\n")
        elif use_chat:
            log.error("Chat mode requires a meeting URL")
            print("\n❌ Chat mode requires a meeting URL:\n")
            print("   python __main__.py --chat <meet-url>\n")
            sys.exit(1)
        else:
            from pipeline.calendar_poller import CalendarPoller
            meeting_queue = queue.Queue()
            poller = CalendarPoller(
                meeting_queue,
                is_busy=lambda: runner._in_meeting,
            )
            poller.start()
            runner.run_polling(meeting_queue)
    except KeyboardInterrupt:
        log.info("Interrupted — leaving meeting")
    finally:
        _shutdown()


def _run_linux(meeting_url, force=False, chat_mode=False):
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

    log.info(f"Starting Operator (Linux) — joining {meeting_url}")
    connector = LinuxAdapter()

    import config
    use_chat = chat_mode or config.INTERACTION_MODE == "chat"

    mcp = None  # only used in chat mode

    if use_chat:
        from openai import OpenAI
        from pipeline.chat_runner import ChatRunner
        from pipeline.llm import LLMClient
        llm = LLMClient(OpenAI(api_key=config.OPENAI_API_KEY), mode="chat")

        if config.MCP_SERVERS:
            from pipeline.mcp_client import MCPClient
            mcp = MCPClient()
            try:
                tool_names = mcp.connect_all()
                log.info(f"MCP tools discovered: {tool_names}")
                llm.inject_mcp_hints(config.MCP_SERVERS)
                loaded = [n for n in config.MCP_SERVERS if n not in mcp.failed_servers]
                llm.inject_mcp_status(loaded, mcp.failed_servers)
                _print_mcp_startup_banner(mcp)
                gh_login = mcp.resolve_github_user()
                if gh_login:
                    llm.inject_github_user(gh_login)
            except Exception as e:
                log.error(f"MCP client startup failed: {e}")
                mcp = None

        runner = ChatRunner(connector, llm, mcp_client=mcp)
    else:
        from pipeline.runner import AgentRunner
        runner = AgentRunner(
            connector=connector,
            tts_output_device="pulse/MeetingOutput",
        )

    _shutdown_called = False

    def _shutdown(signum=None, frame=None):
        nonlocal _shutdown_called
        if _shutdown_called:
            return
        _shutdown_called = True
        if signum:
            log.info(f"Received signal {signum} — shutting down")
        runner.stop()
        if mcp:
            mcp.shutdown()
        connector.leave()
        _kill_orphaned_children()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        runner.run(meeting_url)
        # If run() returned without an explicit stop, the exit was unexpected
        if not runner._stop_event.is_set():
            print(f"\n   Restart with: python __main__.py {'--chat ' if use_chat else ''}{meeting_url}\n")
    except KeyboardInterrupt:
        log.info("Interrupted — leaving meeting")
    finally:
        _shutdown()


if __name__ == "__main__":
    main()
