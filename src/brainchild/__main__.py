"""
Brainchild — AI Meeting Participant
Cross-platform entry point. Auto-detects OS and dispatches to the right adapter.

Usage:
    brainchild <name> <url>     Run named agent in a specific Meet
    brainchild <name>           Auto-open a new Meet, join as that bot
    brainchild try <name>       Terminal test-drive (no Meet)
    brainchild setup            Create a new agent (wizard)
    brainchild                  Print usage + agent list
"""
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

_AGENTS_DIR = Path.home() / ".brainchild" / "agents"
_BUNDLED_AGENTS_DIR = Path(__file__).resolve().parent / "agents"


def _ensure_user_agents():
    """First-run hook: seed ~/.brainchild/agents/ from the bundled agents.

    Runs from main() before any CLI dispatch. If the user agents dir is
    missing or has no bot with a config.yaml, copies every bundled bot
    (pm, engineer, designer) in one shot — single pick framing was a reach,
    so it's all-three-every-time. No-op once the user has at least one bot.
    """
    import shutil
    if not _BUNDLED_AGENTS_DIR.exists():
        return
    if _AGENTS_DIR.exists():
        for p in _AGENTS_DIR.iterdir():
            if p.is_dir() and (p / "config.yaml").is_file():
                return
    _AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    for bundled in _BUNDLED_AGENTS_DIR.iterdir():
        if not bundled.is_dir():
            continue
        dest = _AGENTS_DIR / bundled.name
        if not dest.exists():
            shutil.copytree(bundled, dest)


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
    log = logging.getLogger("brainchild")

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


def _print_startup_banner(skills):
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
    from brainchild import config
    import sys as _sys
    from brainchild.pipeline import face

    bot_name = os.environ.get("BRAINCHILD_BOT", "")
    portrait_path = _AGENTS_DIR / bot_name / "portrait.txt"

    # First-run hook — contributor-added bot with no portrait gets one minted.
    if bot_name and not portrait_path.exists():
        if face.write_if_missing(bot_name, portrait_path):
            import logging
            logging.getLogger("brainchild").info(
                f"minted fresh portrait: {portrait_path}"
            )

    face_text = face.load_or_render(bot_name, portrait_path=portrait_path)
    face_lines = face_text.split("\n")

    sep = " · "
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
    if not _AGENTS_DIR.exists():
        return []
    return sorted(
        p.name for p in _AGENTS_DIR.iterdir()
        if p.is_dir() and (p / "config.yaml").exists()
    )


def _bot_tagline(name):
    # Prefer the explicit agent.tagline in config.yaml; fall back to the first
    # non-header line of README.md for older bots that pre-date the field.
    cfg = _AGENTS_DIR / name / "config.yaml"
    if cfg.exists():
        try:
            import yaml
            data = yaml.safe_load(cfg.read_text()) or {}
            tag = ((data.get("agent") or {}).get("tagline") or "").strip()
            if tag:
                return tag
        except Exception:
            pass
    readme = _AGENTS_DIR / name / "README.md"
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
    print("  brainchild <name> [url]     Run an agent in a Meet")
    print("  brainchild <name>           Auto-open a new Meet, join as that bot")
    print("  brainchild try <name>       Terminal test-drive (no Meet)")
    print("  brainchild setup            Create a new agent (wizard)")
    print()
    print("Flags:")
    print("  --force                   Retry join even if a session is flagged stuck")
    print()
    bots = _available_bots()
    if bots:
        print("Available bots:")
        for b in bots:
            tag = _bot_tagline(b)
            print(f"  {b:<12} {tag}")


def _run_setup():
    from brainchild.pipeline.setup import run as _wizard_run
    return _wizard_run([])


def main():
    _ensure_user_agents()
    argv = sys.argv[1:]

    if not argv or argv[0] in ("-h", "--help"):
        _print_usage()
        return 0

    first = argv[0]

    if first == "setup":
        if len(argv) > 1:
            print(f"Unexpected argument after 'setup': {argv[1]!r}\n")
            _print_usage()
            return 2
        return _run_setup()
    if first == "try":
        if len(argv) < 2:
            print("Usage: brainchild try <name>\n")
            _print_usage()
            return 2
        return _run_try(argv[1])

    if first.startswith("-"):
        print(f"Unknown option: {first}\n")
        _print_usage()
        return 2

    if first not in _available_bots():
        print(f"Unknown bot or subcommand: {first!r}\n")
        _print_usage()
        return 2

    return _run_bot(first, argv[1:])


def _run_try(name):
    """Terminal test-drive — boot the full pipeline (LLM + MCP + skills) against
    a stdin/stdout connector instead of a Meet. Mirrors _run_macos up to the
    browser join, but synchronous MCP startup (no browser to overlap with) and
    a plain 'chat ready' banner on stderr.
    """
    if name not in _available_bots():
        print(f"Unknown bot: {name!r}\n")
        _print_usage()
        return 2

    # Must land before any `from brainchild import config`.
    os.environ["BRAINCHILD_BOT"] = name

    import logging
    import signal
    import time as _time

    logging.basicConfig(
        filename="/tmp/brainchild.log",
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    # Keep stderr clean — terminal UX is the chat itself. Logs stay in /tmp/brainchild.log.
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)

    log = logging.getLogger("brainchild")

    from brainchild import config
    from brainchild.connectors.terminal import TerminalConnector
    from brainchild.pipeline import ui
    from brainchild.pipeline.chat_runner import ChatRunner
    from brainchild.pipeline.llm import LLMClient
    from brainchild.pipeline.meeting_record import MeetingRecord
    from brainchild.pipeline.providers import build_provider
    from brainchild.pipeline.skills import load_skills

    skills = load_skills(config.SKILLS_PATHS)
    _print_startup_banner(skills)

    llm = LLMClient(build_provider())
    llm.inject_skills(skills, config.SKILLS_PROGRESSIVE_DISCLOSURE)

    mcp = None
    if config.MCP_SERVERS:
        from brainchild.pipeline.mcp_client import MCPClient
        mcp = MCPClient()
        try:
            mcp.connect_all()
            llm.inject_mcp_hints(config.MCP_SERVERS)
            loaded = [n for n in config.MCP_SERVERS if n not in mcp.failed_servers]
            llm.inject_mcp_status(loaded, mcp.failed_servers)
            gh_login = mcp.resolve_github_user()
            if gh_login:
                llm.inject_github_user(gh_login)
        except Exception as e:
            log.error(f"MCP client startup failed: {e}")
            ui.err("MCP startup failed")
            mcp = None

    connector = TerminalConnector(bot_name=config.AGENT_NAME)
    slug = f"terminal-{int(_time.time())}"
    record = MeetingRecord(slug=slug, meta={"mode": "terminal", "bot": name})
    llm.set_record(record)

    print("\nchat ready — type to message, /quit or Ctrl+D to exit\n", file=sys.stderr)

    runner = ChatRunner(
        connector,
        llm,
        mcp_client=mcp,
        meeting_record=record,
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
        runner.run(meeting_url=None)
    except KeyboardInterrupt:
        log.info("Interrupted — exiting terminal test-drive")
    finally:
        _shutdown()
        ui.ok("Goodbye.")
    return 0


def _run_bot(name, rest):
    url = None
    force = False
    for arg in rest:
        if arg == "--force":
            force = True
        elif arg.startswith("-"):
            print(f"Unknown flag: {arg}")
            return 2
        elif url is None:
            url = arg
        else:
            print(f"Unexpected argument: {arg}")
            return 2

    # MUST be set before any `from brainchild import config` fires in the pipeline modules.
    os.environ["BRAINCHILD_BOT"] = name

    if sys.platform == "darwin":
        _run_macos(url, force=force)
    else:
        _run_linux(url, force=force)
    return 0


def _run_macos(meeting_url=None, force=False):
    """Run on macOS — direct URL or meet.new auto-launch."""
    import logging
    import signal
    import threading as _threading
    import time as _time

    logging.basicConfig(
        filename="/tmp/brainchild.log",
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    # Stderr stays reserved for the user-facing narrative (pipeline.ui).
    # Detailed diagnostics live in /tmp/brainchild.log only.
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)

    log = logging.getLogger("brainchild")

    from brainchild import config
    from brainchild.connectors.macos_adapter import MacOSAdapter
    from brainchild.pipeline import ui
    from brainchild.pipeline.chat_runner import ChatRunner
    from brainchild.pipeline.llm import LLMClient
    from brainchild.pipeline.providers import build_provider

    t_start = _time.monotonic()

    # Skills load up-front so inject_skills lands before MCP hints/status in
    # the system prompt, and so the banner can show skill count before MCP
    # connects. Banner prints immediately after, as the boot splash.
    from brainchild.pipeline.skills import load_skills
    skills = load_skills(config.SKILLS_PATHS)
    _print_startup_banner(skills)
    ui.say("Launching Chrome…")

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
        from brainchild.pipeline.meeting_record import MeetingRecord, slug_from_url
        from brainchild.pipeline.transcript import TranscriptFinalizer
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
        from brainchild.pipeline.mcp_client import MCPClient
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
            ui.err("meet.new did not produce a meeting URL")
            connector.leave()
            _kill_orphaned_children()
            return
        log.info(f"meet.new resolved to {meeting_url}")
        ui.ok(f"Fresh meeting: {meeting_url}")
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
        reason_file = os.path.join(config.BROWSER_PROFILE_DIR, ".brainchild.kill_reason")
        try:
            with open(reason_file) as _f:
                reason = _f.read().strip()
            os.remove(reason_file)
            ui.warn(reason)
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
        log.info(f"Starting Brainchild — joining {meeting_url}")
        runner.run(meeting_url)
        if not runner._stop_event.is_set():
            ui.say(f"Restart with: brainchild {os.environ.get('BRAINCHILD_BOT', '<name>')} {meeting_url}")
    except KeyboardInterrupt:
        log.info("Interrupted — leaving meeting")
    finally:
        _shutdown()
        ui.ok("Left meeting — goodbye.")


def _run_linux(meeting_url, force=False):
    """Run on Linux — requires a meeting URL and a live DISPLAY."""
    import logging
    import signal

    logging.basicConfig(
        filename="/tmp/brainchild.log",
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)
    log = logging.getLogger("brainchild")

    if not meeting_url:
        meeting_url = os.environ.get("MEETING_URL")
    if not meeting_url:
        bot = os.environ.get("BRAINCHILD_BOT", "<name>")
        print("A meeting URL is required on Linux:", file=sys.stderr)
        print(f"   brainchild {bot} <meet-url>", file=sys.stderr)
        print(f"   MEETING_URL=<url> brainchild {bot}", file=sys.stderr)
        sys.exit(1)

    display = os.environ.get("DISPLAY")
    if not display:
        log.error("DISPLAY is not set")
        print("DISPLAY is not set — start Xvfb first:", file=sys.stderr)
        print("   Xvfb :99 -screen 0 1920x1080x24 &", file=sys.stderr)
        print("   export DISPLAY=:99", file=sys.stderr)
        sys.exit(1)
    log.info(f"DISPLAY={display}")

    from brainchild.connectors.linux_adapter import LinuxAdapter
    from brainchild.pipeline import ui
    from brainchild.pipeline.chat_runner import ChatRunner
    from brainchild.pipeline.llm import LLMClient
    from brainchild.pipeline.providers import build_provider
    from brainchild import config

    from brainchild.pipeline.skills import load_skills
    skills = load_skills(config.SKILLS_PATHS)
    _print_startup_banner(skills)
    ui.say("Launching Chromium…")

    log.info(f"Starting Brainchild (Linux) — joining {meeting_url}")
    connector = LinuxAdapter()
    llm = LLMClient(build_provider())
    llm.inject_skills(skills, config.SKILLS_PROGRESSIVE_DISCLOSURE)

    mcp = None
    if config.MCP_SERVERS:
        from brainchild.pipeline.mcp_client import MCPClient
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
            ui.err("MCP startup failed")
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
            ui.say(f"Restart with: brainchild {os.environ.get('BRAINCHILD_BOT', '<name>')} {meeting_url}")
    except KeyboardInterrupt:
        log.info("Interrupted — leaving meeting")
    finally:
        _shutdown()
        ui.ok("Left meeting — goodbye.")


if __name__ == "__main__":
    sys.exit(main() or 0)
