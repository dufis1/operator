"""
Tests for pipeline.skills loader + LLMClient/ChatRunner wiring.
Run: python tests/test_skills.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("OPERATOR_BOT", "pm")

import logging
import tempfile
from pathlib import Path
from unittest.mock import MagicMock


def _write_skill(folder: Path, name: str, description: str, body: str = "do the thing", extra_fm: str = ""):
    folder.mkdir(parents=True, exist_ok=True)
    fm = f"---\nname: {name}\ndescription: {description}\n"
    if extra_fm:
        fm += extra_fm
    fm += "---\n"
    (folder / "SKILL.md").write_text(fm + body)


def test_happy_path_single_folder():
    from pipeline.skills import load_skills

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "commit"
        _write_skill(root, "commit", "Create a git commit.", body="Run git commit.")
        skills = load_skills([str(root)])
        assert len(skills) == 1
        assert skills[0].name == "commit"
        assert skills[0].description == "Create a git commit."
        assert "Run git commit." in skills[0].body
    print("  happy path single: PASS")


def test_parent_dir_scan():
    from pipeline.skills import load_skills

    with tempfile.TemporaryDirectory() as tmp:
        parent = Path(tmp)
        _write_skill(parent / "a", "alpha", "A skill.")
        _write_skill(parent / "b", "beta", "B skill.")
        skills = load_skills([str(parent)])
        names = sorted(s.name for s in skills)
        assert names == ["alpha", "beta"], names
    print("  parent dir scan: PASS")


def test_missing_path_warns_no_crash(caplog_list):
    from pipeline.skills import load_skills
    skills = load_skills(["/nonexistent/path/that/does/not/exist"])
    assert skills == []
    assert any("path not found" in r.getMessage() for r in caplog_list), "missing path should WARN"
    print("  missing path warn: PASS")


def test_malformed_yaml_skipped(caplog_list):
    from pipeline.skills import load_skills

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "bad"
        root.mkdir()
        # YAML that fails to parse inside frontmatter.
        (root / "SKILL.md").write_text("---\nname: x\n:::broken\n---\nbody\n")
        skills = load_skills([str(root)])
        assert skills == []
        assert any("malformed YAML" in r.getMessage() for r in caplog_list)
    print("  malformed yaml skipped: PASS")


def test_missing_fields_skipped(caplog_list):
    from pipeline.skills import load_skills

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "incomplete"
        root.mkdir()
        (root / "SKILL.md").write_text("---\nname: x\n---\nbody\n")  # no description
        skills = load_skills([str(root)])
        assert skills == []
        assert any("missing name or description" in r.getMessage() for r in caplog_list)
    print("  missing fields skipped: PASS")


def test_duplicate_last_wins(caplog_list):
    from pipeline.skills import load_skills

    with tempfile.TemporaryDirectory() as tmp:
        a = Path(tmp) / "a" / "commit"
        b = Path(tmp) / "b" / "commit"
        _write_skill(a, "commit", "First", body="first body")
        _write_skill(b, "commit", "Second", body="second body")
        skills = load_skills([str(a), str(b)])
        assert len(skills) == 1
        assert skills[0].body.startswith("second body"), skills[0].body
        assert any("duplicate name 'commit'" in r.getMessage() for r in caplog_list)
    print("  duplicate last-wins: PASS")


def test_deep_nesting_ignored():
    from pipeline.skills import load_skills

    with tempfile.TemporaryDirectory() as tmp:
        parent = Path(tmp)
        # Two-levels deep — should be ignored by parent-dir scan.
        _write_skill(parent / "outer" / "inner", "deep", "Too deep.")
        skills = load_skills([str(parent)])
        assert skills == [], f"expected no skills, got {[s.name for s in skills]}"
    print("  deep nesting ignored: PASS")


def test_allowed_tools_warn(caplog_list):
    from pipeline.skills import load_skills

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "with-tools"
        _write_skill(
            root, "tool-skill", "Uses tools.",
            extra_fm="allowed-tools: [Bash, Edit, load_skill]\n",
        )
        skills = load_skills([str(root)])
        assert len(skills) == 1
        assert "load_skill" in skills[0].allowed_tools
        assert "Bash" in skills[0].allowed_tools  # still loaded, just warned
        assert any("unsupported allowed-tools" in r.getMessage() for r in caplog_list)
    print("  allowed-tools warn: PASS")


def test_llm_inject_skills_menu():
    """Progressive mode puts the menu (names + descriptions) in the system prompt, not the bodies."""
    from pipeline.llm import LLMClient
    from pipeline.skills import Skill

    llm = LLMClient(MagicMock())
    base = llm._system_prompt
    skills = [
        Skill(name="commit", description="Create a commit.", body="secret body"),
    ]
    llm.inject_skills(skills, progressive=True)
    sys_text = llm._system_prompt
    assert "commit: Create a commit." in sys_text
    assert "secret body" not in sys_text, "menu mode should NOT include full bodies"
    assert "load_skill" in sys_text
    print("  llm inject_skills menu: PASS")


def test_llm_inject_skills_full():
    """Non-progressive mode inlines the full skill body."""
    from pipeline.llm import LLMClient
    from pipeline.skills import Skill

    llm = LLMClient(MagicMock())
    skills = [Skill(name="x", description="d", body="FULL BODY TEXT")]
    llm.inject_skills(skills, progressive=False)
    assert "FULL BODY TEXT" in llm._system_prompt
    print("  llm inject_skills full: PASS")


def test_load_skill_tool_only_when_progressive_with_skills():
    """_tools_for_llm exposes load_skill iff progressive_disclosure AND skills loaded."""
    from pipeline.chat_runner import ChatRunner, LOAD_SKILL_TOOL
    from pipeline.skills import Skill

    # No skills — no load_skill tool (even when MCP is absent, we return None).
    runner = ChatRunner(connector=MagicMock(), llm=MagicMock(), mcp_client=None, skills=[])
    assert runner._tools_for_llm() is None

    # Skills + progressive on — load_skill appears.
    runner = ChatRunner(
        connector=MagicMock(), llm=MagicMock(), mcp_client=None,
        skills=[Skill(name="a", description="d", body="b")],
        skills_progressive=True,
    )
    tools = runner._tools_for_llm()
    assert tools and any(t["function"]["name"] == LOAD_SKILL_TOOL for t in tools)

    # Skills + progressive off — no load_skill.
    runner = ChatRunner(
        connector=MagicMock(), llm=MagicMock(), mcp_client=None,
        skills=[Skill(name="a", description="d", body="b")],
        skills_progressive=False,
    )
    tools = runner._tools_for_llm()
    assert tools is None or all(t["function"]["name"] != LOAD_SKILL_TOOL for t in tools)
    print("  load_skill tool gating: PASS")


def test_call_rate_sanity():
    """Stubbed LLM: across 6 chat turns (none slash-invoking a skill), no code path
    should count a slash-invoke. This is the regression hook for watching whether
    the model over-calls load_skill. Real call-rate is provider-driven — we only
    verify our local counters don't increment on unrelated messages here."""
    from pipeline.chat_runner import ChatRunner
    from pipeline.skills import Skill

    runner = ChatRunner(
        connector=MagicMock(), llm=MagicMock(), mcp_client=None,
        skills=[
            Skill(name="commit", description="Create a git commit.", body="..."),
            Skill(name="review", description="Review a PR.", body="..."),
        ],
        skills_progressive=True,
    )
    # Make the stubbed LLM return plain text (no tool_call).
    runner._llm.ask = MagicMock(return_value="ok")

    messages = [
        "hey how are you",
        "what's 2+2",
        "tell me a joke",
        "thanks",
        "what time is it",
        "bye",
    ]
    for m in messages:
        runner._handle_message(m)

    assert runner._load_skill_calls == 0, \
        f"non-slash chit-chat should not count as load_skill calls, got {runner._load_skill_calls}"
    assert runner._turn_count == len(messages)
    print("  call-rate sanity: PASS")


def test_slash_invocation_counts_as_load():
    from pipeline.chat_runner import ChatRunner
    from pipeline.skills import Skill

    runner = ChatRunner(
        connector=MagicMock(), llm=MagicMock(), mcp_client=None,
        skills=[Skill(name="commit", description="d", body="commit instructions")],
        skills_progressive=True,
    )
    runner._llm.ask = MagicMock(return_value="ok")

    runner._handle_message("/commit please")
    assert runner._load_skill_calls == 1
    assert runner._load_skill_by_name == {"commit": 1}
    # extra_system kwarg should carry the skill body.
    kwargs = runner._llm.ask.call_args.kwargs
    assert "commit instructions" in kwargs["extra_system"]

    # Unknown slash should be a pass-through, no counter bump.
    runner._handle_message("/unknown thing")
    assert runner._load_skill_calls == 1
    print("  slash-invocation counts: PASS")


# ---------------------------------------------------------------------------
# Gap-fill tests (session 133) — branches not covered by the tests above
# ---------------------------------------------------------------------------

def test_no_frontmatter_skipped(caplog_list):
    """File that doesn't start with '---' is skipped with a 'missing frontmatter' warning."""
    from pipeline.skills import load_skills

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "no-fm"
        root.mkdir()
        (root / "SKILL.md").write_text("just body, no frontmatter at all\n")
        skills = load_skills([str(root)])
        assert skills == []
        assert any("missing frontmatter" in r.getMessage() for r in caplog_list)
    print("  no frontmatter skipped: PASS")


def test_unterminated_frontmatter_skipped(caplog_list):
    """'---' opens but is never closed → skipped with 'unterminated frontmatter' warning."""
    from pipeline.skills import load_skills

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "unterminated"
        root.mkdir()
        (root / "SKILL.md").write_text("---\nname: x\ndescription: y\nbody goes here no closing\n")
        skills = load_skills([str(root)])
        assert skills == []
        assert any("unterminated frontmatter" in r.getMessage() for r in caplog_list)
    print("  unterminated frontmatter skipped: PASS")


def test_non_dict_frontmatter_skipped(caplog_list):
    """Frontmatter that parses as a list (or any non-mapping) is skipped."""
    from pipeline.skills import load_skills

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "listy"
        root.mkdir()
        (root / "SKILL.md").write_text("---\n- name: x\n- description: y\n---\nbody\n")
        skills = load_skills([str(root)])
        assert skills == []
        assert any("not a mapping" in r.getMessage() for r in caplog_list)
    print("  non-dict frontmatter skipped: PASS")


def test_allowed_tools_string_parses_comma_split():
    """allowed-tools declared as a comma-separated string is split and loaded."""
    from pipeline.skills import load_skills

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "string-tools"
        _write_skill(
            root, "s", "Uses comma-string tools.",
            extra_fm="allowed-tools: 'load_skill, Bash, Edit'\n",
        )
        skills = load_skills([str(root)])
        assert len(skills) == 1
        assert skills[0].allowed_tools == ["load_skill", "Bash", "Edit"]
    print("  allowed-tools comma string: PASS")


def test_empty_parent_folder_warns(caplog_list):
    """Parent folder with no SKILL.md anywhere → warns 'no SKILL.md found' and returns []."""
    from pipeline.skills import load_skills

    with tempfile.TemporaryDirectory() as tmp:
        parent = Path(tmp)
        (parent / "empty-subdir").mkdir()  # subdir with no SKILL.md
        (parent / "another").mkdir()
        skills = load_skills([str(parent)])
        assert skills == []
        assert any("no SKILL.md found" in r.getMessage() for r in caplog_list)
    print("  empty parent folder warns: PASS")


class _CapturingHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []
    def emit(self, record):
        self.records.append(record)


def _run_with_caplog(test_fn):
    """Run a test function that accepts a caplog_list (log records)."""
    handler = _CapturingHandler()
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    prev_level = root.level
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)
    try:
        test_fn(handler.records)
    finally:
        root.removeHandler(handler)
        root.setLevel(prev_level)


if __name__ == "__main__":
    print("Running skill tests...")
    test_happy_path_single_folder()
    test_parent_dir_scan()
    _run_with_caplog(test_missing_path_warns_no_crash)
    _run_with_caplog(test_malformed_yaml_skipped)
    _run_with_caplog(test_missing_fields_skipped)
    _run_with_caplog(test_duplicate_last_wins)
    test_deep_nesting_ignored()
    _run_with_caplog(test_allowed_tools_warn)
    test_llm_inject_skills_menu()
    test_llm_inject_skills_full()
    test_load_skill_tool_only_when_progressive_with_skills()
    test_call_rate_sanity()
    test_slash_invocation_counts_as_load()
    # Gap-fill (session 133)
    _run_with_caplog(test_no_frontmatter_skipped)
    _run_with_caplog(test_unterminated_frontmatter_skipped)
    _run_with_caplog(test_non_dict_frontmatter_skipped)
    test_allowed_tools_string_parses_comma_split()
    _run_with_caplog(test_empty_parent_folder_warns)
    print("\nAll skill tests passed.")
