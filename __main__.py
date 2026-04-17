"""
Operator — AI Meeting Participant
Cross-platform entry point. Auto-detects OS and dispatches to the right adapter.

Usage:
    operator <name> <url>     Run named roster bot in a specific Meet
    operator <name>           Auto-open a new Meet, join as that bot
    operator setup            Create a new roster bot (wizard)
    operator list             Show available roster bots
    operator                  Print usage + roster list
"""
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

_ROOT = Path(__file__).parent
_ROSTER_DIR = _ROOT / "roster"


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

    labeled = []
    for cpid in child_pids:
        try:
            r = _sp.run(
                ["ps", "-o", "command=", "-p", str(cpid)],
                capture_output=True, text=True, timeout=1,
                start_new_session=False,
            )
            cmd = r.stdout.strip().replace("\n", " ")
        except Exception:
            cmd = ""
        labeled.append(f"{cpid} ({cmd})" if cmd else str(cpid))
    log.warning(f"Safety net: killing {len(child_pids)} orphaned child process(es): [{', '.join(labeled)}]")

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


def _print_startup_banner(skills, plain=False):
    """Print the face + identity + loadout banner as the boot splash.

    Must fire BEFORE MCP / browser startup logs so it sits at the top of the
    terminal like a fighter-select splash, not buried mid-scroll. MCP server
    names come from config (known without connecting); per-server ✓/✗ status
    is left to the existing connect logs rather than duplicated here.

        ▄▄▄▄▄▄   <AgentName>
        █ ▲▲ █   <tagline>
        █ ══ █   linear · github · 4 skills · claude-sonnet-4-5
        ▀▀▀▀▀▀

    Also triggers the first-run portrait hook: any bot without a committed
    portrait.txt gets one minted from the deterministic glyph generator.
    """
    import config
    import sys as _sys
    from pipeline import face

    bot_name = os.environ.get("OPERATOR_BOT", "")
    portrait_path = _ROSTER_DIR / bot_name / "portrait.txt"

    # First-run hook — contributor-added bot with no portrait gets one minted.
    # Skip in --plain mode so the ASCII fallback doesn't get persisted as the
    # canonical look for a bot that just happened to boot on a hostile terminal.
    if bot_name and not plain and not portrait_path.exists():
        if face.write_if_missing(bot_name, portrait_path):
            import logging
            logging.getLogger("operator").info(
                f"minted fresh portrait: {portrait_path}"
            )

    if plain:
        face_text = face.render(bot_name, plain=True)
    else:
        face_text = face.load_or_render(bot_name, portrait_path=portrait_path)
    face_lines = face_text.split("\n")

    sep = " | " if plain else " · "
    parts = list(config.MCP_SERVERS.keys())
    n_skills = len(skills) if skills else 0
    if n_skills:
        parts.append(f"{n_skills} skills")
    parts.append(config.LLM_MODEL)
    loadout = sep.join(parts)

    right = [config.AGENT_NAME, config.AGENT_TAGLINE, loadout, ""]

    gap = "   "
    print("", file=_sys.stderr)
    for fl, rt in zip(face_lines, right):
        print(f"{fl}{gap}{rt}".rstrip(), file=_sys.stderr)
    print("", file=_sys.stderr)


def _available_bots():
    if not _ROSTER_DIR.exists():
        return []
    return sorted(
        p.name for p in _ROSTER_DIR.iterdir()
        if p.is_dir() and (p / "config.yaml").exists()
    )


def _bot_tagline(name):
    # Prefer the explicit agent.tagline in config.yaml; fall back to the first
    # non-header line of README.md for older bots that pre-date the field.
    cfg = _ROSTER_DIR / name / "config.yaml"
    if cfg.exists():
        try:
            import yaml
            data = yaml.safe_load(cfg.read_text()) or {}
            tag = ((data.get("agent") or {}).get("tagline") or "").strip()
            if tag:
                return tag
        except Exception:
            pass
    readme = _ROSTER_DIR / name / "README.md"
    if not readme.exists():
        return ""
    lines = readme.read_text().splitlines()
    seen_h1 = False
    for line in lines:
        stripped = line.strip()
        if not seen_h1:
            if stripped.startswith("# "):
                seen_h1 = True
            continue
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""


def _print_usage():
    print("Usage:")
    print("  operator <name> [url]     Run a roster bot in a Meet")
    print("  operator <name>           Auto-open a new Meet, join as that bot")
    print("  operator setup            Create a new roster bot (wizard)")
    print("  operator list             Show available bots")
    print()
    print("Flags:")
    print("  --plain                   ASCII-only banner (screen readers / hostile terminals)")
    print("  --force                   Retry join even if a session is flagged stuck")
    print("  --check-mcp               Start MCP servers, print tool counts, exit")
    print()
    bots = _available_bots()
    if bots:
        print("Available bots:")
        for b in bots:
            tag = _bot_tagline(b)
            print(f"  {b:<12} {tag}")


def _run_list():
    bots = _available_bots()
    if not bots:
        print("No roster bots found.")
        return 0
    for b in bots:
        tag = _bot_tagline(b)
        print(f"  {b:<12} {tag}")
    return 0


def _run_setup(rest):
    print("operator setup — wizard not yet implemented (Phase 15.5.5).")
    print("For now, create a new bot by copying roster/pm/ and editing it.")
    return 0


def main():
    argv = sys.argv[1:]

    if not argv or argv[0] in ("-h", "--help"):
        _print_usage()
        return 0

    first = argv[0]

    if first == "setup":
        return _run_setup(argv[1:])
    if first == "list":
        return _run_list()

    if first.startswith("-"):
        print(f"Unknown option: {first}\n")
        _print_usage()
        return 2

    if first not in _available_bots():
        print(f"Unknown bot or subcommand: {first!r}\n")
        _print_usage()
        return 2

    return _run_bot(first, argv[1:])


def _run_bot(name, rest):
    url = None
    force = False
    check_mcp = False
    plain = False
    for arg in rest:
        if arg == "--force":
            force = True
        elif arg == "--check-mcp":
            check_mcp = True
        elif arg == "--plain":
            plain = True
        elif arg.startswith("-"):
            print(f"Unknown flag: {arg}")
            return 2
        elif url is None:
            url = arg
        else:
            print(f"Unexpected argument: {arg}")
            return 2

    # MUST be set before any `import config` fires in the pipeline modules.
    os.environ["OPERATOR_BOT"] = name

    if check_mcp:
        return _check_mcp()

    if sys.platform == "darwin":
        _run_macos(url, force=force, plain=plain)
    else:
        _run_linux(url, force=force, plain=plain)
    return 0


def _run_macos(meeting_url=None, force=False, plain=False):
    """Run on macOS — direct URL or meet.new auto-launch."""
    import logging
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

    # Skills load up-front so inject_skills lands before MCP hints/status in
    # the system prompt, and so the banner can show skill count before MCP
    # connects. Banner prints immediately after, as the boot splash.
    from pipeline.skills import load_skills
    skills = load_skills(config.SKILLS_PATHS)
    _print_startup_banner(skills, plain=plain)

    connector = MacOSAdapter(force=force)
    llm = LLMClient(build_provider())
    llm.inject_skills(skills, config.SKILLS_PROGRESSIVE_DISCLOSURE)

    # Captions → MeetingRecord wiring. The JS bridge (window.__onCaption) is
    # exposed by MacOSAdapter at browser startup whenever config.CAPTIONS_ENABLED
    # is true, so set_caption_callback is safe to call before OR after
    # connector.join(). meet.new mode late-binds after the URL resolves.
    def _wire_meeting_record(url):
        if not config.CAPTIONS_ENABLED:
            return None, None
        from pipeline.meeting_record import MeetingRecord, slug_from_url
        from pipeline.transcript import TranscriptFinalizer
        slug = slug_from_url(url)
        record = MeetingRecord(slug=slug, meta={"meet_url": url})
        llm.set_record(record)
        finalizer = TranscriptFinalizer(record, silence_seconds=config.CAPTION_SILENCE_SECONDS)
        connector.set_caption_callback(finalizer.on_caption_update)
        log.info("captions enabled — transcript will be appended to meeting record")
        return record, finalizer

    meeting_record = None
    transcript_finalizer = None
    if meeting_url:
        meeting_record, transcript_finalizer = _wire_meeting_record(meeting_url)

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

    # meet.new mode: wait for the browser to redirect and publish the real URL.
    if meeting_url is None:
        meeting_url = connector.wait_for_resolved_url(timeout=45)
        if not meeting_url:
            log.error("meet.new did not produce a meeting URL — exiting")
            connector.leave()
            _kill_orphaned_children()
            return
        log.info(f"meet.new resolved to {meeting_url}")
        print(f"Fresh meeting: {meeting_url}")
        # The bot joins in a headless Chrome — pop the Meet open in the
        # user's default browser so they can see and chat with the bot.
        try:
            webbrowser.open(meeting_url)
        except Exception as e:
            log.warning(f"could not auto-open meeting URL in browser: {e}")
        meeting_record, transcript_finalizer = _wire_meeting_record(meeting_url)

    mcp = None
    if mcp_thread:
        mcp_thread.join()
        mcp = _mcp_result["client"]
        if mcp:
            llm.inject_mcp_hints(config.MCP_SERVERS)
            loaded = [n for n in config.MCP_SERVERS if n not in mcp.failed_servers]
            llm.inject_mcp_status(loaded, mcp.failed_servers)
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
        connector.leave()
        _kill_orphaned_children()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        log.info(f"Starting Operator — joining {meeting_url}")
        runner.run(meeting_url)
        if not runner._stop_event.is_set():
            print(f"\n   Restart with: operator {os.environ.get('OPERATOR_BOT', '<name>')} {meeting_url}\n")
    except KeyboardInterrupt:
        log.info("Interrupted — leaving meeting")
    finally:
        _shutdown()


def _run_linux(meeting_url, force=False, plain=False):
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
        bot = os.environ.get("OPERATOR_BOT", "<name>")
        print("\n❌ A meeting URL is required on Linux:\n")
        print(f"   operator {bot} <meet-url>")
        print(f"   MEETING_URL=<url> operator {bot}\n")
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

    from pipeline.skills import load_skills
    skills = load_skills(config.SKILLS_PATHS)
    _print_startup_banner(skills, plain=plain)

    log.info(f"Starting Operator (Linux) — joining {meeting_url}")
    connector = LinuxAdapter()
    llm = LLMClient(build_provider())
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
            print(f"\n   Restart with: operator {os.environ.get('OPERATOR_BOT', '<name>')} {meeting_url}\n")
    except KeyboardInterrupt:
        log.info("Interrupted — leaving meeting")
    finally:
        _shutdown()


if __name__ == "__main__":
    sys.exit(main() or 0)
