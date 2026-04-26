"""
Tests for the voice mode redesign — plain vs technical across the
prompt + narrator surfaces. The third surface (reply content) is
LLM-prompted via ground_rules and is not unit-testable from here.

Run: python tests/test_voice_modes.py
"""
import importlib
import os
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import MagicMock


sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))


def _load_config(yaml_text):
    """Boot brainchild.config with a temporary agents/fakebot/config.yaml.

    Override $HOME so config.py reads our scratch dir. Wipe cached config
    so each call gets a fresh read.
    """
    tmp = tempfile.mkdtemp()
    bot_dir = Path(tmp) / ".brainchild" / "agents" / "fakebot"
    bot_dir.mkdir(parents=True)
    (bot_dir / "config.yaml").write_text(textwrap.dedent(yaml_text))
    (Path(tmp) / ".brainchild" / ".env").write_text("")
    os.environ["BRAINCHILD_BOT"] = "fakebot"
    os.environ["HOME"]           = tmp
    for mod in list(sys.modules):
        if mod == "brainchild.config":
            del sys.modules[mod]
    return importlib.import_module("brainchild.config")


def _yaml(voice_line=""):
    """Compose a minimal valid YAML, optionally with a voice line."""
    return f"""
        agent:
          name: "FakeBot"
          trigger_phrase: "@fakebot"
          {voice_line}
        llm:
          provider: "openai"
          model: "gpt-5"
        mcp_servers: {{}}
        ground_rules: ""
        personality: ""
    """


def test_voice_plain_is_default():
    cfg = _load_config(_yaml())
    assert cfg.VOICE == "plain"


def test_voice_technical_explicit():
    cfg = _load_config(_yaml("voice: technical"))
    assert cfg.VOICE == "technical"


def test_legacy_permission_verbosity_translates():
    """terse → plain, verbose → technical, with a deprecation log."""
    yaml = """
        agent:
          name: "FakeBot"
          trigger_phrase: "@fakebot"
          permission_verbosity: terse
        llm:
          provider: "openai"
          model: "gpt-5"
        mcp_servers: {}
        ground_rules: ""
        personality: ""
    """
    cfg = _load_config(yaml)
    assert cfg.VOICE == "plain"

    yaml = yaml.replace("permission_verbosity: terse", "permission_verbosity: verbose")
    cfg = _load_config(yaml)
    assert cfg.VOICE == "technical"


def test_format_plain_prompts():
    """Per-tool plain phrases have the right shape and embed imperative fields."""
    from brainchild.pipeline.permission_chat_handler import _format_plain
    assert "shell command" in _format_plain("Bash", {"command": "ls"})
    assert "`ls`"          in _format_plain("Bash", {"command": "ls"})
    assert "read the file" in _format_plain("Read", {"file_path": "/tmp/x.py"})
    assert "/tmp/x.py"     in _format_plain("Read", {"file_path": "/tmp/x.py"})
    assert "write a new file at" in _format_plain("Write", {"file_path": "/tmp/y.py", "content": "hi"})
    assert "fetch"         in _format_plain("WebFetch", {"url": "https://example.com/x"})
    assert "https://example.com/x" in _format_plain("WebFetch", {"url": "https://example.com/x"})


def test_format_plain_mcp_translation():
    """MCP tools get translated via the friendly-name + verb convention."""
    from brainchild.pipeline.permission_chat_handler import _format_plain
    out = _format_plain("mcp__sentry__get_sentry_resource", {"url": "https://x"})
    assert "Sentry" in out
    assert "look up" in out

    out = _format_plain("mcp__claude_ai_Linear__save_issue", {})
    assert "Linear" in out
    assert "save" in out


def test_format_plain_unknown_tool_falls_back_with_imperative_visible():
    """Unknown tool with imperative field still surfaces the field value."""
    from brainchild.pipeline.permission_chat_handler import _format_plain
    out = _format_plain("SomeBrandNewTool", {"url": "https://important.com/path"})
    assert "SomeBrandNewTool" in out
    assert "https://important.com/path" in out


def test_url_imperative_field_never_collapsed_in_terse():
    """The Sentry URL bug: terse mode must keep URLs verbatim, not collapse to (89 B)."""
    from brainchild.pipeline.permission_chat_handler import _format_terse
    long_url = "https://brainchild-3z.sentry.io/issues/7441991509/events/" + "x" * 50
    out = _format_terse("mcp__sentry__get_sentry_resource", {"url": long_url})
    assert long_url in out, f"URL was collapsed to size hint: {out!r}"
    assert "B)" not in out, f"URL hidden as size hint: {out!r}"


def test_format_confirmation_plain_mode():
    """End-to-end: voice=plain produces 'Want me to ...?' framing."""
    _load_config(_yaml("voice: plain"))
    if "brainchild.pipeline.permission_chat_handler" in sys.modules:
        del sys.modules["brainchild.pipeline.permission_chat_handler"]
    from brainchild.pipeline.permission_chat_handler import _format_confirmation
    out = _format_confirmation("Bash", {"command": "ls /tmp"})
    assert out.startswith("Want me to")
    assert "(yes/no)" in out
    assert "ls /tmp" in out


def test_format_confirmation_technical_mode():
    """End-to-end: voice=technical produces 'Run X?\\n  • ...' framing."""
    _load_config(_yaml("voice: technical"))
    if "brainchild.pipeline.permission_chat_handler" in sys.modules:
        del sys.modules["brainchild.pipeline.permission_chat_handler"]
    from brainchild.pipeline.permission_chat_handler import _format_confirmation
    out = _format_confirmation("Bash", {"command": "ls /tmp"})
    assert out.startswith("Run Bash?")
    assert "command:" in out
    assert "OK?" in out


def test_narrator_plain_picks_one_status_per_batch():
    """The plain narrator collapses N tool calls into one conversational line."""
    from brainchild.pipeline.chat_runner import ChatRunner
    # Three reads + one grep should collapse to "Reading through the code..."
    line = ChatRunner._narrator_plain([
        ("Read", {"file_path": "/a"}),
        ("Read", {"file_path": "/b"}),
        ("Grep", {"pattern": "foo"}),
    ])
    assert "code" in line.lower()

    line = ChatRunner._narrator_plain([("ToolSearch", {"query": "select:foo"})])
    assert "tools" in line.lower()

    line = ChatRunner._narrator_plain([
        ("mcp__sentry__get_sentry_resource", {"url": "https://x"}),
    ])
    assert "Sentry" in line

    line = ChatRunner._narrator_plain([("WebFetch", {"url": "https://x"})])
    assert "online" in line.lower() or "web" in line.lower()


if __name__ == "__main__":
    fns = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        try:
            fn()
            print(f"  ✓ {fn.__name__}")
        except AssertionError as e:
            print(f"  ✗ {fn.__name__}: {e}")
            sys.exit(1)
    print(f"\n{len(fns)} tests passed.")
