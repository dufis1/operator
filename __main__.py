"""
Operator — AI Meeting Participant
Cross-platform entry point. Auto-detects OS and dispatches to the right adapter.

Usage:
    python __main__.py              # macOS: calendar auto-join
    python __main__.py <meet-url>   # Join a specific meeting (required on Linux)
    python .                        # same as above from the repo root
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
    """Last-resort cleanup: kill any child processes that survived graceful shutdown."""
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
            os.kill(cpid, 0)
            os.kill(cpid, _sig.SIGKILL)
            log.warning(f"Safety net: SIGKILL sent to pid {cpid}")
        except ProcessLookupError:
            pass


def _check_mcp() -> int:
    """Validate MCP config: start each server, list tools, print summary, exit."""
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
    """Print a one-line MCP status banner to stderr."""
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
            "Linux: meeting URL is required."
        ),
    )
    parser.add_argument(
        "meeting_url",
        nargs="?",
        metavar="MEET_URL",
        help="Google Meet URL to join (required on Linux)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Kill any existing Operator session and start a new one",
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
        _run_macos(args.meeting_url, force=args.force)
    else:
        _run_linux(args.meeting_url, force=args.force)


def _run_macos(meeting_url=None, force=False):
    """Run on macOS — calendar polling or direct URL."""
    import logging
    import queue
    import signal
    import threading as _threading
    import time as _time

    logging.basicConfig(
        filename="/tmp/operator.log",
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    _console = logging.StreamHandler(sys.stderr)
    _console.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d %(message)s", datefmt="%H:%M:%S"))
    logging.getLogger().addHandler(_console)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)

    log = logging.getLogger("operator")

    import config
    from connectors.macos_adapter import MacOSAdapter
    from pipeline.chat_runner import ChatRunner
    from pipeline.llm import LLMClient
    from pipeline.providers import build_provider

    t_start = _time.monotonic()
    connector = MacOSAdapter(force=force)
    llm = LLMClient(build_provider())

    # Skills load up-front so inject_skills lands before MCP hints/status in the system prompt.
    from pipeline.skills import load_skills
    skills = load_skills(config.SKILLS_PATHS)
    llm.inject_skills(skills, config.SKILLS_PROGRESSIVE_DISCLOSURE)

    # Captions → MeetingRecord wiring.
    #
    # The JS bridge (window.__onCaption) is exposed by MacOSAdapter at browser
    # startup whenever config.CAPTIONS_ENABLED is true, so set_caption_callback
    # is safe to call before OR after connector.join(). Direct-URL mode wires
    # the finalizer up-front here. Calendar mode wires per-meeting once a URL
    # arrives via the calendar queue (see runner.run_polling), reusing the
    # already-exposed bridge.
    meeting_record = None
    transcript_finalizer = None
    if config.CAPTIONS_ENABLED and meeting_url:
        from pipeline.meeting_record import MeetingRecord, slug_from_url
        from pipeline.transcript import TranscriptFinalizer
        slug = slug_from_url(meeting_url)
        meeting_record = MeetingRecord(slug=slug, meta={"meet_url": meeting_url})
        llm.set_record(meeting_record)
        transcript_finalizer = TranscriptFinalizer(
            meeting_record, silence_seconds=config.CAPTION_SILENCE_SECONDS
        )
        connector.set_caption_callback(transcript_finalizer.on_caption_update)
        log.info("captions enabled — transcript will be appended to meeting record")
    elif config.CAPTIONS_ENABLED:
        log.info("captions enabled — bridge will expose at browser startup; calendar runner wires per-meeting finalizer")

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

    connector.join(meeting_url)

    mcp = None
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

    log.info(f"TIMING setup={_time.monotonic() - t_start:.1f}s")
    runner = ChatRunner(
        connector,
        llm,
        mcp_client=mcp,
        meeting_record=meeting_record,
        skills=skills,
        skills_progressive=config.SKILLS_PROGRESSIVE_DISCLOSURE,
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
        if transcript_finalizer:
            transcript_finalizer.stop()
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
            if not runner._stop_event.is_set():
                print(f"\n   Restart with: python __main__.py {meeting_url}\n")
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


def _run_linux(meeting_url, force=False):
    """Run on Linux — requires a meeting URL and a live DISPLAY."""
    import logging
    import signal

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    log = logging.getLogger("operator")

    if not meeting_url:
        meeting_url = os.environ.get("MEETING_URL")
    if not meeting_url:
        print("\n❌ A meeting URL is required on Linux:\n")
        print("   python __main__.py <meet-url>")
        print("   MEETING_URL=<url> python __main__.py\n")
        sys.exit(1)

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

    from connectors.linux_adapter import LinuxAdapter
    from pipeline.chat_runner import ChatRunner
    from pipeline.llm import LLMClient
    from pipeline.providers import build_provider
    import config

    log.info(f"Starting Operator (Linux) — joining {meeting_url}")
    connector = LinuxAdapter()
    llm = LLMClient(build_provider())

    from pipeline.skills import load_skills
    skills = load_skills(config.SKILLS_PATHS)
    llm.inject_skills(skills, config.SKILLS_PROGRESSIVE_DISCLOSURE)

    mcp = None
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

    runner = ChatRunner(
        connector,
        llm,
        mcp_client=mcp,
        skills=skills,
        skills_progressive=config.SKILLS_PROGRESSIVE_DISCLOSURE,
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
        if not runner._stop_event.is_set():
            print(f"\n   Restart with: python __main__.py {meeting_url}\n")
    except KeyboardInterrupt:
        log.info("Interrupted — leaving meeting")
    finally:
        _shutdown()


if __name__ == "__main__":
    main()
