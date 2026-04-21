"""Tests for `brainchild setup` wizard (pipeline/setup.py).

Covers the individual helpers rather than end-to-end prompting — the rich
prompts are easy to wire interactively but noisy to mock at scale, and the
interesting failure modes live in the helpers (skill copy, MCP flip,
atomic write, collision validation, edit-in-place swap).

Run: python tests/test_setup.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

# pipeline.setup does not import config.py, but some sibling imports might
# — set a safe default so tests never fail on missing BRAINCHILD_BOT.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("BRAINCHILD_BOT", "pm")

from pipeline import setup as wizard  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────


def _make_skill_folder(root: Path, name: str, desc: str = "test skill") -> Path:
    """Create root/<name>/SKILL.md with minimal valid frontmatter."""
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n\nbody\n",
        encoding="utf-8",
    )
    return d


# ── 1. Name validation (collision + reserved) ─────────────────────────────


def test_name_validation_reserved_and_collision():
    """_validate_name rejects reserved CLI verbs, bad chars, and existing bots."""
    # reserved
    ok, reason = wizard._validate_name("setup")
    assert not ok and "reserved" in reason.lower(), reason
    ok, reason = wizard._validate_name("list")
    assert not ok and "reserved" in reason.lower(), reason

    # bad shape — starts with digit, too long, contains space, empty
    for bad in ("1bot", "A" * 33, "a b", "", "  "):
        ok, _ = wizard._validate_name(bad)
        assert not ok, f"expected rejection for {bad!r}"

    # collision — pm always exists in this repo
    ok, reason = wizard._validate_name("pm")
    assert not ok and "already exists" in reason, reason

    # happy path
    ok, reason = wizard._validate_name("brand-new-bot")
    assert ok, reason

    print("  name validation: PASS")


# ── 2. MCP enabled-flag round-trip ────────────────────────────────────────


def test_mcp_enabled_flip_round_trip():
    """Toggling a server's `enabled` flag survives yaml dump → load → config.py filter."""
    cfg = wizard._load_yaml(wizard._PM_CONFIG)
    servers = cfg["mcp_servers"]
    # Flip all servers: on→off and off→on.
    for name in servers:
        servers[name]["enabled"] = not bool(servers[name].get("enabled", False))
    expected_enabled = {n for n, s in servers.items() if s["enabled"]}

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "config.yaml"
        wizard._dump_yaml(cfg, out)
        roundtrip = wizard._load_yaml(out)

    # Simulate config.py's enabled filter.
    filtered = {
        n: s for n, s in roundtrip["mcp_servers"].items()
        if s.get("enabled", True)
    }
    assert set(filtered) == expected_enabled, (filtered, expected_enabled)
    print("  mcp enabled flip round-trip: PASS")


# ── 3. Skill copy — folder, single-file wrap, parent walk ─────────────────


def test_skill_copy_folder():
    """A folder with SKILL.md copies through with its folder name intact."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        src = _make_skill_folder(root / "srcs", "my-skill")
        dst_root = root / "dst" / "skills"
        wizard._copy_user_skill(src, dst_root)
        assert (dst_root / "my-skill" / "SKILL.md").is_file()
    print("  skill copy (folder): PASS")


def test_skill_copy_single_md_wrap():
    """A single .md file gets wrapped in a stem-named folder as SKILL.md."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        md = root / "lone-skill.md"
        md.write_text("---\nname: lone\ndescription: d\n---\nbody\n", encoding="utf-8")
        dst_root = root / "dst" / "skills"
        wizard._copy_user_skill(md, dst_root)
        wrapped = dst_root / "lone-skill" / "SKILL.md"
        assert wrapped.is_file(), wrapped
        assert "body" in wrapped.read_text(encoding="utf-8")
    print("  skill copy (single file wrap): PASS")


def test_skill_copy_parent_walk():
    """A parent folder copies each child that has SKILL.md."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        parent = root / "srcs"
        parent.mkdir()
        _make_skill_folder(parent, "alpha")
        _make_skill_folder(parent, "beta")
        # Non-skill sibling should be ignored.
        (parent / "not-a-skill").mkdir()
        dst_root = root / "dst" / "skills"
        wizard._copy_user_skill(parent, dst_root)
        assert (dst_root / "alpha" / "SKILL.md").is_file()
        assert (dst_root / "beta" / "SKILL.md").is_file()
        assert not (dst_root / "not-a-skill").exists()
    print("  skill copy (parent walk): PASS")


# ── 4. Atomic write — rollback on build failure ──────────────────────────


def _make_state(name: str, mode: str = "edit", **overrides) -> "wizard.WizardState":
    """Build a minimally-valid WizardState for write/reveal tests."""
    bot_cfg = overrides.pop("bot_cfg", None) or {
        "agent": {"name": name.capitalize(), "trigger_phrase": "@brainchild"},
        "llm": {"provider": "anthropic", "model": "x"},
        "mcp_servers": {},
    }
    defaults = dict(
        mode=mode,
        name=name,
        display_name=name.capitalize(),
        tagline="",
        based_on="pm" if mode == "new" else name,
        portrait="placeholder",
        bot_cfg=bot_cfg,
        user_sources=[],
        bundled_skill_dirs=[],
    )
    defaults.update(overrides)
    return wizard.WizardState(**defaults)


def test_atomic_write_rollback_on_build_failure():
    """If build fails mid-flight, the target dir is untouched and no tmpdir lingers."""
    with tempfile.TemporaryDirectory() as tmp:
        sandbox = Path(tmp) / "agents"
        sandbox.mkdir()
        original_agents_dir = wizard._AGENTS_DIR
        wizard._AGENTS_DIR = sandbox
        try:
            target = sandbox / "victim"
            target.mkdir()
            (target / "sentinel.txt").write_text("keep-me", encoding="utf-8")

            # Bogus user source → _copy_user_skill raises mid-build.
            bogus = Path(tmp) / "nope"
            state = _make_state("victim", mode="edit", user_sources=[bogus])

            raised = False
            try:
                wizard._step7_write(state)
            except Exception:
                raised = True

            assert raised, "expected exception on bogus source"
            assert (target / "sentinel.txt").read_text(encoding="utf-8") == "keep-me"
            stray = [p for p in sandbox.iterdir() if p.name.startswith(".victim.tmp-")]
            assert not stray, f"tempdir not cleaned up: {stray}"
            baks = [p for p in sandbox.iterdir() if p.name.startswith("victim.bak-")]
            assert not baks, f"backup left behind: {baks}"
        finally:
            wizard._AGENTS_DIR = original_agents_dir
    print("  atomic write rollback: PASS")


# ── 5. Edit-in-place swap — .bak cleaned on success ──────────────────────


def test_edit_in_place_swap_cleans_backup():
    """Successful edit-in-place renames old dir to .bak-<ts>, then deletes .bak."""
    with tempfile.TemporaryDirectory() as tmp:
        sandbox = Path(tmp) / "agents"
        sandbox.mkdir()
        original_agents_dir = wizard._AGENTS_DIR
        wizard._AGENTS_DIR = sandbox
        try:
            target = sandbox / "preset"
            target.mkdir()
            (target / ".env.example").write_text("KEY=val", encoding="utf-8")
            (target / "README.md").write_text("# Hand-written\n", encoding="utf-8")
            (target / "skills").mkdir()
            (target / "skills" / "deselected").mkdir()

            state = _make_state("preset", mode="edit")
            out = wizard._step7_write(state)
            assert out == target
            assert (target / "config.yaml").is_file()
            assert (target / ".env.example").read_text(encoding="utf-8") == "KEY=val"
            assert "Hand-written" in (target / "README.md").read_text(encoding="utf-8")
            assert not (target / "skills" / "deselected").exists()
            baks = [p for p in sandbox.iterdir() if p.name.startswith("preset.bak-")]
            assert not baks, f"backup not cleaned: {baks}"
        finally:
            wizard._AGENTS_DIR = original_agents_dir
    print("  edit-in-place swap: PASS")


# ── 6. From-scratch write — new bundle created ────────────────────────────


def test_from_scratch_write_creates_bundle():
    """mode='new' with a free target writes a complete bundle."""
    with tempfile.TemporaryDirectory() as tmp:
        sandbox = Path(tmp) / "agents"
        sandbox.mkdir()
        original_agents_dir = wizard._AGENTS_DIR
        wizard._AGENTS_DIR = sandbox
        try:
            bot_cfg = {
                "agent": {"name": "Fresh", "trigger_phrase": "@brainchild", "tagline": "t"},
                "llm": {"provider": "anthropic", "model": "x"},
                "mcp_servers": {"notion": {"enabled": True, "command": "npx", "args": []}},
            }
            state = _make_state("fresh", mode="new", bot_cfg=bot_cfg)
            out = wizard._step7_write(state)
            assert out.is_dir()
            assert (out / "config.yaml").is_file()
            assert (out / "portrait.txt").is_file()
            assert (out / "README.md").is_file()
            loaded = wizard._load_yaml(out / "config.yaml")
            assert loaded["skills"]["paths"] == []
        finally:
            wizard._AGENTS_DIR = original_agents_dir
    print("  from-scratch write: PASS")


# ── 7. .env append — never overwrites existing keys ───────────────────────


def test_env_append_preserves_existing():
    """_append_env adds new keys to .env without overwriting existing ones."""
    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp) / ".env"
        env.write_text("EXISTING='already-set'\n", encoding="utf-8")

        wizard._append_env(env, {"NEW_KEY": "new-value"})

        parsed = wizard._parse_env(env)
        assert parsed["EXISTING"] == "already-set"
        assert parsed["NEW_KEY"] == "new-value"
    print("  env append preserves existing: PASS")


# ── 8. WizardState — equipped views + card render ────────────────────────


def test_wizard_state_equipped_views():
    """equipped_mcps reflects `enabled` flags; equipped_skills concatenates
    user-supplied names + bundled folder names."""
    cfg = {
        "mcp_servers": {
            "linear": {"enabled": True},
            "notion": {"enabled": False},
            "github": {"enabled": True},
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Two bundled skill dirs and one user-added folder.
        bundled1 = _make_skill_folder(root / "b", "alpha")
        bundled2 = _make_skill_folder(root / "b", "beta")
        user = _make_skill_folder(root / "u", "user-skill")

        state = wizard.WizardState(
            mode="new",
            name="researcher",
            display_name="Researcher",
            tagline="t",
            based_on="pm",
            portrait="placeholder",
            bot_cfg=cfg,
            user_sources=[user],
            bundled_skill_dirs=[bundled1, bundled2],
        )
        assert state.equipped_mcps() == ["linear", "github"]
        assert state.equipped_skills() == ["user-skill", "alpha", "beta"]
        # Card renders without raising; smoke test.
        assert state.card() is not None
        assert state.card(mcps=["only-this"]) is not None
    print("  wizard state equipped views: PASS")


# ── 9. _resolve_user_skill_names — three input shapes ────────────────────


def test_resolve_user_skill_names():
    """Resolves to: <stem> for .md, <name> for SKILL.md folder,
    each child for parent walks."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        md = root / "lone.md"
        md.write_text("---\nname: lone\ndescription: d\n---\n", encoding="utf-8")
        folder = _make_skill_folder(root, "single-folder")
        parent = root / "many"
        parent.mkdir()
        _make_skill_folder(parent, "child-a")
        _make_skill_folder(parent, "child-b")

        names = wizard._resolve_user_skill_names([md, folder, parent])
        assert "lone" in names
        assert "single-folder" in names
        assert "child-a" in names
        assert "child-b" in names
    print("  resolve user skill names: PASS")


# ── 10. Reveal — placeholder swaps for real portrait ─────────────────────


def test_reveal_swaps_portrait():
    """_reveal mutates state.portrait from placeholder to the real face."""
    with tempfile.TemporaryDirectory() as tmp:
        sandbox = Path(tmp) / "agents"
        sandbox.mkdir()
        original_agents_dir = wizard._AGENTS_DIR
        wizard._AGENTS_DIR = sandbox
        try:
            # Pre-create the portrait file that _reveal will read.
            target = sandbox / "fresh"
            target.mkdir()
            from pipeline import face
            (target / "portrait.txt").write_text(face.render("fresh") + "\n", encoding="utf-8")

            from pipeline import build_card
            state = _make_state("fresh", mode="new")
            assert state.portrait == "placeholder"
            state.portrait = build_card.PLACEHOLDER_PORTRAIT
            wizard._reveal(state)
            assert state.portrait != build_card.PLACEHOLDER_PORTRAIT
            assert "█" in state.portrait, "expected real face glyphs after reveal"
        finally:
            wizard._AGENTS_DIR = original_agents_dir
    print("  reveal swaps portrait: PASS")


# ── 11. Picker — driven by injected key_source ───────────────────────────


def test_picker_select_one_with_key_source():
    """select_one navigates with UP/DOWN and returns the chosen Choice."""
    import readchar
    from pipeline.picker import Choice, select_one
    choices = [Choice(label=f"item{i}", value=i) for i in range(3)]
    keys = [readchar.key.DOWN, readchar.key.DOWN, readchar.key.ENTER]
    picked = select_one("pick", choices, key_source=keys)
    assert picked.value == 2
    print("  picker select_one: PASS")


def test_picker_select_many_with_key_source():
    """select_many navigates with UP/DOWN, toggles with SPACE, confirms with ENTER."""
    import readchar
    from pipeline.picker import Choice, select_many
    choices = [Choice(label=f"item{i}") for i in range(3)]
    # Start at 0, toggle item0 on, move down, toggle item1 on, confirm.
    keys = [
        " ",
        readchar.key.DOWN,
        " ",
        readchar.key.ENTER,
    ]
    out = select_many("pick", choices, key_source=keys)
    assert out == [True, True, False]
    print("  picker select_many: PASS")


def test_picker_cancels_on_q():
    """select_one raises PickerCancelled when 'q' is pressed."""
    from pipeline.picker import Choice, PickerCancelled, select_one
    choices = [Choice(label="x")]
    raised = False
    try:
        select_one("pick", choices, key_source=["q"])
    except PickerCancelled:
        raised = True
    assert raised, "expected PickerCancelled on 'q'"
    print("  picker cancels on q: PASS")


# ── 12. _parse_env edge cases (S1) ────────────────────────────────────────


def test_parse_env_strips_quotes():
    """Both single- and double-quoted values are unwrapped to the raw value."""
    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp) / ".env"
        env.write_text(
            "DOUBLE=\"value-1\"\n"
            "SINGLE='value-2'\n"
            "BARE=value-3\n",
            encoding="utf-8",
        )
        parsed = wizard._parse_env(env)
        assert parsed == {"DOUBLE": "value-1", "SINGLE": "value-2", "BARE": "value-3"}, parsed
    print("  parse_env strips quotes: PASS")


def test_parse_env_skips_comments_and_blanks():
    """`#` comments and blank lines are ignored without error."""
    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp) / ".env"
        env.write_text(
            "# leading comment\n"
            "\n"
            "KEY_A=a\n"
            "   \n"
            "# trailing comment\n"
            "KEY_B=b\n",
            encoding="utf-8",
        )
        parsed = wizard._parse_env(env)
        assert parsed == {"KEY_A": "a", "KEY_B": "b"}, parsed
    print("  parse_env skips comments + blanks: PASS")


def test_parse_env_tolerates_malformed_line():
    """A line without `=` is silently dropped — does not raise."""
    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp) / ".env"
        env.write_text(
            "NOT_AN_ASSIGNMENT\n"
            "VALID=ok\n",
            encoding="utf-8",
        )
        parsed = wizard._parse_env(env)
        assert parsed == {"VALID": "ok"}, parsed
    print("  parse_env tolerates malformed: PASS")


# ── 13. _append_env creates missing file (S2) ─────────────────────────────


def test_append_env_creates_missing_file():
    """Non-existent .env is created with the new values appended."""
    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp) / "new.env"
        assert not env.exists()
        wizard._append_env(env, {"FRESH_KEY": "fresh-value"})
        assert env.is_file()
        parsed = wizard._parse_env(env)
        assert parsed == {"FRESH_KEY": "fresh-value"}, parsed
    print("  append_env creates missing file: PASS")


# ── 14. _collect_env_refs (S3) ────────────────────────────────────────────


def test_collect_env_refs_from_enabled_servers():
    """${VAR} refs are gathered from enabled servers' env dict."""
    cfg = {
        "mcp_servers": {
            "linear": {
                "enabled": True,
                "env": {"LINEAR_API_KEY": "${LINEAR_API_KEY}"},
            },
            "github": {
                "enabled": True,
                "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}", "OTHER": "literal"},
            },
        },
    }
    state = wizard.WizardState(
        mode="new", name="x", display_name="X", tagline="", based_on="pm",
        portrait="placeholder", bot_cfg=cfg, user_sources=[], bundled_skill_dirs=[],
    )
    refs = wizard._collect_env_refs(state)
    assert refs == {"LINEAR_API_KEY", "GITHUB_TOKEN"}, refs
    print("  collect_env_refs enabled only: PASS")


def test_collect_env_refs_skips_disabled_servers():
    """Refs from disabled servers are NOT collected — we don't prompt for keys
    the wizard is turning off."""
    cfg = {
        "mcp_servers": {
            "on-server": {
                "enabled": True,
                "env": {"KEEP_THIS": "${KEEP_THIS}"},
            },
            "off-server": {
                "enabled": False,
                "env": {"DROP_THIS": "${DROP_THIS}"},
            },
        },
    }
    state = wizard.WizardState(
        mode="new", name="x", display_name="X", tagline="", based_on="pm",
        portrait="placeholder", bot_cfg=cfg, user_sources=[], bundled_skill_dirs=[],
    )
    refs = wizard._collect_env_refs(state)
    assert refs == {"KEEP_THIS"}, refs
    print("  collect_env_refs skips disabled: PASS")


def test_collect_env_refs_empty_when_no_mcps():
    """A bot with no mcp_servers section yields the empty set."""
    state = wizard.WizardState(
        mode="new", name="x", display_name="X", tagline="", based_on="pm",
        portrait="placeholder", bot_cfg={}, user_sources=[], bundled_skill_dirs=[],
    )
    assert wizard._collect_env_refs(state) == set()
    print("  collect_env_refs empty when no mcps: PASS")


# ── 15. _is_valid_skill_source (S4) ───────────────────────────────────────


def test_is_valid_skill_source_accepts_three_shapes():
    """.md file, SKILL.md folder, and parent-of-SKILL.md all validate."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        md = root / "lone.md"
        md.write_text("---\nname: lone\ndescription: d\n---\n", encoding="utf-8")
        folder = _make_skill_folder(root, "single")
        parent = root / "parent"
        parent.mkdir()
        _make_skill_folder(parent, "kid")

        assert wizard._is_valid_skill_source(md) is True, "md file should validate"
        assert wizard._is_valid_skill_source(folder) is True, "SKILL.md folder should validate"
        assert wizard._is_valid_skill_source(parent) is True, "parent-of-SKILL.md should validate"
    print("  is_valid_skill_source accepts three shapes: PASS")


def test_is_valid_skill_source_rejects_unrelated_paths():
    """Random folder without SKILL.md and non-md file return False."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        empty_dir = root / "empty"
        empty_dir.mkdir()
        txt = root / "notes.txt"
        txt.write_text("not a skill", encoding="utf-8")
        readme = root / "README.md"  # md file but let's see: _is_valid accepts any .md
        readme.write_text("plain readme", encoding="utf-8")

        assert wizard._is_valid_skill_source(empty_dir) is False
        assert wizard._is_valid_skill_source(txt) is False
        # README.md is technically accepted by the .md shape — confirm so
        # future readers know the validator doesn't re-check frontmatter here.
        assert wizard._is_valid_skill_source(readme) is True
    print("  is_valid_skill_source rejects non-skill paths: PASS")


# ── 16. build_card.render + _wrap_cells (S5) ──────────────────────────────


def test_build_card_render_panel_mode():
    """rainbow=False returns a Rich Panel titled with the given title."""
    from pipeline import build_card
    from rich.panel import Panel

    out = build_card.render(
        name="Pm",
        tagline="task wrangler",
        portrait=build_card.PLACEHOLDER_PORTRAIT,
        power_ups=["linear", "github"],
        skills=["review"],
        title="Your build",
        rainbow=False,
    )
    assert isinstance(out, Panel), f"expected Panel, got {type(out).__name__}"
    assert out.title == "Your build"
    # Render body content to a string so we can sanity-check the fields landed.
    body_plain = out.renderable.plain if hasattr(out.renderable, "plain") else str(out.renderable)
    assert "Pm" in body_plain
    assert "task wrangler" in body_plain
    assert "linear" in body_plain and "github" in body_plain
    assert "review" in body_plain
    print("  build_card render Panel mode: PASS")


def test_build_card_render_rainbow_emits_ansi():
    """rainbow=True returns a Text built from ANSI — contains escape codes."""
    from pipeline import build_card
    from rich.text import Text

    out = build_card.render(
        name="Fresh",
        tagline="reveal",
        portrait=build_card.PLACEHOLDER_PORTRAIT,
        power_ups=[],
        skills=[],
        rainbow=True,
    )
    assert isinstance(out, Text), f"expected Text, got {type(out).__name__}"
    # Text.from_ansi strips the escape codes into Text spans; the plain form
    # should still contain the frame-plus-title glyphs.
    plain = out.plain
    assert "Your build" in plain
    assert "╭" in plain and "╯" in plain, f"expected rainbow frame glyphs, got: {plain[:100]!r}"
    print("  build_card render rainbow path: PASS")


def test_build_card_empty_bullets_show_placeholder():
    """Empty power_ups/skills render as the '—' placeholder in the body rows."""
    from pipeline import build_card

    rows = build_card._compose_body(
        name="X", tagline="t", portrait=build_card.PLACEHOLDER_PORTRAIT,
        power_ups=[], skills=[],
    )
    joined = "\n".join(rows)
    assert "power-ups:  —" in joined, f"expected em-dash for empty power_ups, got: {joined!r}"
    assert "skills:     —" in joined, f"expected em-dash for empty skills, got: {joined!r}"
    print("  build_card empty bullets show em-dash: PASS")


def test_wrap_cells_hard_splits_oversized_token():
    """A single token wider than the width is split on code-point boundaries,
    not dropped or overflowing."""
    from pipeline import build_card
    long_word = "x" * 25
    out = build_card._wrap_cells(long_word, 10)
    # All rows ≤ 10 cells; concatenation reconstructs the input.
    assert all(len(r) <= 10 for r in out), out
    assert "".join(out) == long_word
    # Normal case: short-enough text returns as-is.
    assert build_card._wrap_cells("hi", 10) == ["hi"]
    print("  wrap_cells hard-splits wide token: PASS")


# ── 17. _first_line (S6) ──────────────────────────────────────────────────


def test_first_line_basic_truncation_and_empty():
    """Picks the first non-empty line; truncates with ellipsis past max_chars;
    empty input returns empty string."""
    # Skips leading blank lines
    assert wizard._first_line("\n\nhello world\nsecond\n") == "hello world"
    # Truncation adds an ellipsis
    long = "a" * 80
    out = wizard._first_line(long, max_chars=20)
    assert out.endswith("…"), out
    assert len(out) == 20, out  # max_chars-1 chars + "…" = max_chars
    # Empty / whitespace-only input
    assert wizard._first_line("") == ""
    assert wizard._first_line("   \n\t\n") == ""
    print("  first_line basic + truncate + empty: PASS")


if __name__ == "__main__":
    print("Setup wizard tests:")
    test_name_validation_reserved_and_collision()
    test_mcp_enabled_flip_round_trip()
    test_skill_copy_folder()
    test_skill_copy_single_md_wrap()
    test_skill_copy_parent_walk()
    test_atomic_write_rollback_on_build_failure()
    test_edit_in_place_swap_cleans_backup()
    test_from_scratch_write_creates_bundle()
    test_env_append_preserves_existing()
    test_wizard_state_equipped_views()
    test_resolve_user_skill_names()
    test_reveal_swaps_portrait()
    test_picker_select_one_with_key_source()
    test_picker_select_many_with_key_source()
    test_picker_cancels_on_q()
    # Session 134 gap-fill
    test_parse_env_strips_quotes()
    test_parse_env_skips_comments_and_blanks()
    test_parse_env_tolerates_malformed_line()
    test_append_env_creates_missing_file()
    test_collect_env_refs_from_enabled_servers()
    test_collect_env_refs_skips_disabled_servers()
    test_collect_env_refs_empty_when_no_mcps()
    test_is_valid_skill_source_accepts_three_shapes()
    test_is_valid_skill_source_rejects_unrelated_paths()
    test_build_card_render_panel_mode()
    test_build_card_render_rainbow_emits_ansi()
    test_build_card_empty_bullets_show_placeholder()
    test_wrap_cells_hard_splits_oversized_token()
    test_first_line_basic_truncation_and_empty()
    print("\nAll tests passed.")
