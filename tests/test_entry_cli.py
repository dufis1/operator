"""
Unit tests for the Entry component (Boundary depth).

Covers `__main__.py`:
  Bot discovery
    - _available_bots: filter to dirs with config.yaml, sorted
    - _bot_tagline: yaml agent.tagline preferred; README fallback; neither → ""
  main() dispatch
    - no args / -h → usage, returns 0
    - `setup` → _run_setup()
    - `setup` with extra args → returns 2
    - `try <name>` → _run_try(name)
    - `try` with no name → returns 2
    - unknown flag → returns 2
    - unknown bot/subcommand → returns 2
    - known bot → _run_bot(name, rest)
  _run_bot arg parsing
    - url + --force parsed; sets BRAINCHILD_BOT
  _run_try validation
    - unknown bot → returns 2 before any heavy imports

Approach: each test rebuilds a tmp `agents/` tree and monkey-patches the
module-level `_AGENTS_DIR`, plus patches dispatch targets (_run_setup,
_run_try, _run_bot, _run_macos, _run_linux) so we never actually boot the
pipeline.

Run:
    source venv/bin/activate
    python tests/test_entry_cli.py
"""
import io
import os
import sys
import tempfile
import shutil
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

# Loading __main__.py by name imports the module without re-executing as a
# script (the `if __name__ == "__main__"` guard is skipped).
import importlib.util
_SPEC = importlib.util.spec_from_file_location(
    "brainchild_entry",
    Path(__file__).resolve().parent.parent / "src" / "brainchild" / "__main__.py",
)
entry = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(entry)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@contextmanager
def tmp_agents_dir(bots):
    """Build a tmp agents/ tree.

    `bots` is a dict: {bot_name: {"yaml": str | None, "readme": str | None}}.
    If yaml is not None, config.yaml is written. Same for README.md. A bot
    whose yaml is None is written without config.yaml so _available_bots
    will filter it out.
    """
    tmp = Path(tempfile.mkdtemp())
    agents = tmp / "agents"
    agents.mkdir()
    for name, spec in bots.items():
        d = agents / name
        d.mkdir()
        if spec.get("yaml") is not None:
            (d / "config.yaml").write_text(spec["yaml"])
        if spec.get("readme") is not None:
            (d / "README.md").write_text(spec["readme"])
    saved = entry._AGENTS_DIR
    entry._AGENTS_DIR = agents
    try:
        yield agents
    finally:
        entry._AGENTS_DIR = saved
        shutil.rmtree(tmp, ignore_errors=True)


@contextmanager
def patched_argv(argv_after_prog):
    saved = sys.argv
    sys.argv = ["brainchild"] + list(argv_after_prog)
    try:
        yield
    finally:
        sys.argv = saved


@contextmanager
def patched_dispatch(**overrides):
    """Patch module-level dispatch functions to spies so main() doesn't
    actually run the pipeline. Restored on exit.
    """
    saved = {name: getattr(entry, name) for name in overrides}
    for name, func in overrides.items():
        setattr(entry, name, func)
    try:
        yield
    finally:
        for name, func in saved.items():
            setattr(entry, name, func)


# ---------------------------------------------------------------------------
# Bot discovery
# ---------------------------------------------------------------------------

def test_available_bots_filters_and_sorts():
    """Only dirs containing config.yaml are returned; result is sorted."""
    bots = {
        "zebra": {"yaml": "agent: {name: zebra}"},
        "alpha": {"yaml": "agent: {name: alpha}"},
        "missing_config": {"yaml": None},   # dir without config.yaml → skipped
    }
    with tmp_agents_dir(bots) as agents:
        # Add a stray *file* at agents-level — must not be treated as a bot
        (agents / "notabot.txt").write_text("hi")
        result = entry._available_bots()
    assert result == ["alpha", "zebra"], result
    print("PASS  test_available_bots_filters_and_sorts")


def test_available_bots_empty_when_dir_missing():
    saved = entry._AGENTS_DIR
    entry._AGENTS_DIR = Path("/nonexistent/path/that/does/not/exist")
    try:
        result = entry._available_bots()
    finally:
        entry._AGENTS_DIR = saved
    assert result == []
    print("PASS  test_available_bots_empty_when_dir_missing")


def test_bot_tagline_prefers_yaml():
    """yaml agent.tagline wins over README fallback."""
    yaml_text = "agent:\n  name: pm\n  tagline: Project manager bot\n"
    readme = "# pm\n\nReadme tagline (should NOT be used)\n"
    with tmp_agents_dir({"pm": {"yaml": yaml_text, "readme": readme}}):
        result = entry._bot_tagline("pm")
    assert result == "Project manager bot", result
    print("PASS  test_bot_tagline_prefers_yaml")


def test_bot_tagline_falls_back_to_readme():
    """When yaml has no tagline field, first non-header README line is used."""
    yaml_text = "agent: {name: pm}\n"   # no tagline
    readme = "# pm\n\nThe readme tagline line.\n\nMore text\n"
    with tmp_agents_dir({"pm": {"yaml": yaml_text, "readme": readme}}):
        result = entry._bot_tagline("pm")
    assert result == "The readme tagline line.", result
    print("PASS  test_bot_tagline_falls_back_to_readme")


def test_bot_tagline_empty_when_neither():
    """No yaml tagline and no README → empty string."""
    yaml_text = "agent: {name: pm}\n"
    with tmp_agents_dir({"pm": {"yaml": yaml_text}}):
        result = entry._bot_tagline("pm")
    assert result == "", repr(result)
    print("PASS  test_bot_tagline_empty_when_neither")


# ---------------------------------------------------------------------------
# main() dispatch
# ---------------------------------------------------------------------------

def test_main_no_args_prints_usage_and_returns_zero():
    with tmp_agents_dir({"pm": {"yaml": "agent: {name: pm}"}}):
        with patched_argv([]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = entry.main()
    assert rc == 0
    assert "Usage:" in buf.getvalue()
    print("PASS  test_main_no_args_prints_usage_and_returns_zero")


def test_main_help_flag_prints_usage_and_returns_zero():
    with tmp_agents_dir({"pm": {"yaml": "agent: {name: pm}"}}):
        for flag in ("-h", "--help"):
            with patched_argv([flag]):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = entry.main()
            assert rc == 0, flag
            assert "Usage:" in buf.getvalue(), flag
    print("PASS  test_main_help_flag_prints_usage_and_returns_zero")


def test_main_setup_dispatches_with_no_args():
    """setup subcommand invokes _run_setup with no arguments."""
    spy = MagicMock(return_value=0)
    with tmp_agents_dir({"pm": {"yaml": "agent: {name: pm}"}}):
        with patched_argv(["setup"]), patched_dispatch(_run_setup=spy):
            rc = entry.main()
    assert rc == 0
    assert spy.call_count == 1
    assert spy.call_args.args == ()
    print("PASS  test_main_setup_dispatches_with_no_args")


def test_main_setup_rejects_extra_args():
    """Extra positional/flag args after `setup` return 2 with usage."""
    spy = MagicMock(return_value=0)
    with tmp_agents_dir({"pm": {"yaml": "agent: {name: pm}"}}):
        with patched_argv(["setup", "--from", "pm"]), patched_dispatch(_run_setup=spy):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = entry.main()
    assert rc == 2
    assert spy.call_count == 0
    assert "Unexpected argument" in buf.getvalue()
    print("PASS  test_main_setup_rejects_extra_args")


def test_main_try_dispatches_with_name():
    spy = MagicMock(return_value=0)
    with tmp_agents_dir({"pm": {"yaml": "agent: {name: pm}"}}):
        with patched_argv(["try", "pm"]), patched_dispatch(_run_try=spy):
            rc = entry.main()
    assert rc == 0
    assert spy.call_args.args == ("pm",)
    print("PASS  test_main_try_dispatches_with_name")


def test_main_try_without_name_returns_2():
    with tmp_agents_dir({"pm": {"yaml": "agent: {name: pm}"}}):
        with patched_argv(["try"]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = entry.main()
    assert rc == 2
    assert "Usage:" in buf.getvalue()
    print("PASS  test_main_try_without_name_returns_2")


def test_main_unknown_flag_returns_2():
    with tmp_agents_dir({"pm": {"yaml": "agent: {name: pm}"}}):
        with patched_argv(["--nonsense"]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = entry.main()
    assert rc == 2
    assert "Unknown option" in buf.getvalue()
    print("PASS  test_main_unknown_flag_returns_2")


def test_main_unknown_bot_returns_2():
    with tmp_agents_dir({"pm": {"yaml": "agent: {name: pm}"}}):
        with patched_argv(["ghost"]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = entry.main()
    assert rc == 2
    assert "Unknown bot or subcommand" in buf.getvalue()
    print("PASS  test_main_unknown_bot_returns_2")


def test_main_known_bot_dispatches_to_run_bot():
    """Known bot + positional url → _run_bot(name, rest)."""
    spy = MagicMock(return_value=0)
    with tmp_agents_dir({"pm": {"yaml": "agent: {name: pm}"}}):
        with patched_argv(["pm", "https://meet.google.com/abc-defg-hij"]), \
             patched_dispatch(_run_bot=spy):
            rc = entry.main()
    assert rc == 0
    assert spy.call_args.args == ("pm", ["https://meet.google.com/abc-defg-hij"])
    print("PASS  test_main_known_bot_dispatches_to_run_bot")


# ---------------------------------------------------------------------------
# _run_bot arg parsing
# ---------------------------------------------------------------------------

def test_run_bot_parses_url_and_flags_and_sets_env():
    """_run_bot parses url + --force; sets BRAINCHILD_BOT before dispatching."""
    captured = {}
    def fake_macos(meeting_url=None, force=False):
        captured["platform"] = "macos"
        captured["url"] = meeting_url
        captured["force"] = force
        captured["env"] = os.environ.get("BRAINCHILD_BOT")
    def fake_linux(meeting_url, force=False):
        captured["platform"] = "linux"
        captured["url"] = meeting_url
        captured["force"] = force
        captured["env"] = os.environ.get("BRAINCHILD_BOT")

    saved_env = os.environ.get("BRAINCHILD_BOT")
    try:
        os.environ.pop("BRAINCHILD_BOT", None)
        with tmp_agents_dir({"pm": {"yaml": "agent: {name: pm}"}}):
            with patched_dispatch(_run_macos=fake_macos, _run_linux=fake_linux):
                rc = entry._run_bot(
                    "pm",
                    ["https://meet.google.com/abc-defg-hij", "--force"],
                )
        assert rc == 0
        assert captured["url"] == "https://meet.google.com/abc-defg-hij"
        assert captured["force"] is True
        assert captured["env"] == "pm"
    finally:
        if saved_env is None:
            os.environ.pop("BRAINCHILD_BOT", None)
        else:
            os.environ["BRAINCHILD_BOT"] = saved_env
    print("PASS  test_run_bot_parses_url_and_flags_and_sets_env")


def test_run_bot_unknown_flag_returns_2():
    saved_env = os.environ.get("BRAINCHILD_BOT")
    try:
        with tmp_agents_dir({"pm": {"yaml": "agent: {name: pm}"}}):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = entry._run_bot("pm", ["--not-a-real-flag"])
        assert rc == 2
        assert "Unknown flag" in buf.getvalue()
    finally:
        if saved_env is None:
            os.environ.pop("BRAINCHILD_BOT", None)
        else:
            os.environ["BRAINCHILD_BOT"] = saved_env
    print("PASS  test_run_bot_unknown_flag_returns_2")


# ---------------------------------------------------------------------------
# _run_try validation
# ---------------------------------------------------------------------------

def test_run_try_unknown_bot_returns_2():
    """Unknown bot must bail early — before any `from brainchild import config` can fire."""
    with tmp_agents_dir({"pm": {"yaml": "agent: {name: pm}"}}):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = entry._run_try("ghost")
    assert rc == 2
    assert "Unknown bot" in buf.getvalue()
    print("PASS  test_run_try_unknown_bot_returns_2")


# ---------------------------------------------------------------------------
# Package-import smoke test — `[project.scripts] brainchild = "brainchild.__main__:main"`
# wires pip's console script to this exact import path, so it must succeed
# cleanly with no side effects beyond the documented Popen monkeypatch.
# ---------------------------------------------------------------------------

def test_package_import_exposes_main():
    """`import brainchild.__main__` works and `main` is a zero-arg callable."""
    import importlib
    mod = importlib.import_module("brainchild.__main__")
    assert callable(mod.main), "brainchild.__main__.main is not callable"
    import inspect
    sig = inspect.signature(mod.main)
    assert len(sig.parameters) == 0, f"main() must take no args, got {sig}"
    print("PASS  test_package_import_exposes_main")


# ---------------------------------------------------------------------------
# Run all
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_available_bots_filters_and_sorts,
        test_available_bots_empty_when_dir_missing,
        test_bot_tagline_prefers_yaml,
        test_bot_tagline_falls_back_to_readme,
        test_bot_tagline_empty_when_neither,
        test_main_no_args_prints_usage_and_returns_zero,
        test_main_help_flag_prints_usage_and_returns_zero,
        test_main_setup_dispatches_with_no_args,
        test_main_setup_rejects_extra_args,
        test_main_try_dispatches_with_name,
        test_main_try_without_name_returns_2,
        test_main_unknown_flag_returns_2,
        test_main_unknown_bot_returns_2,
        test_main_known_bot_dispatches_to_run_bot,
        test_run_bot_parses_url_and_flags_and_sets_env,
        test_run_bot_unknown_flag_returns_2,
        test_run_try_unknown_bot_returns_2,
        test_package_import_exposes_main,
    ]
    failures = []
    for t in tests:
        try:
            t()
        except Exception as e:
            import traceback
            print(f"FAIL  {t.__name__}: {e}")
            traceback.print_exc()
            failures.append(t.__name__)
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
