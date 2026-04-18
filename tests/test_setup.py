"""Tests for `operator setup` wizard (pipeline/setup.py).

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
# — set a safe default so tests never fail on missing OPERATOR_BOT.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("OPERATOR_BOT", "pm")

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


def test_atomic_write_rollback_on_build_failure(monkeypatch=None):
    """If build fails mid-flight, the target dir is untouched and no tmpdir lingers."""
    # Redirect _AGENTS_DIR to a sandbox for the duration of this test.
    with tempfile.TemporaryDirectory() as tmp:
        sandbox = Path(tmp) / "agents"
        sandbox.mkdir()
        original_agents_dir = wizard._AGENTS_DIR
        wizard._AGENTS_DIR = sandbox
        try:
            # Pre-seed target with a sentinel file we can re-check post-failure.
            target = sandbox / "victim"
            target.mkdir()
            (target / "sentinel.txt").write_text("keep-me", encoding="utf-8")

            # Cause _step5_write to fail by passing a user_sources entry that
            # doesn't exist — _copy_user_skill will raise.
            bogus = Path(tmp) / "nope"
            bot_cfg = {
                "agent": {"name": "Victim"},
                "llm": {"provider": "anthropic", "model": "x", "system_prompt": "p"},
                "connector": {"browser_profile_dir": "./b", "auth_state_file": "./a"},
                "mcp_servers": {},
            }

            raised = False
            try:
                wizard._step5_write(
                    mode="edit",
                    name="victim",
                    bot_cfg=bot_cfg,
                    user_sources=[bogus],
                    bundled_dirs=[],
                )
            except Exception:
                raised = True

            assert raised, "expected exception on bogus source"
            # Target restored, sentinel intact, no tempdir left behind.
            assert (target / "sentinel.txt").read_text(encoding="utf-8") == "keep-me"
            stray = [p for p in sandbox.iterdir() if p.name.startswith(".victim.tmp-")]
            assert not stray, f"tempdir not cleaned up: {stray}"
            # No backup left behind either.
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
            # `.env.example` is a sibling the wizard doesn't own — edit-in-place
            # must preserve it through the swap.
            (target / ".env.example").write_text("KEY=val", encoding="utf-8")
            # A pre-existing README should survive too — hand-written prose is
            # more valuable than the wizard's stub.
            (target / "README.md").write_text("# Hand-written\n", encoding="utf-8")
            # Stale skill folder should be wiped — skills are fully re-authored.
            (target / "skills").mkdir()
            (target / "skills" / "deselected").mkdir()

            bot_cfg = {
                "agent": {"name": "Preset", "trigger_phrase": "@operator"},
                "llm": {"provider": "anthropic", "model": "x", "system_prompt": "p"},
                "connector": {"browser_profile_dir": "./b", "auth_state_file": "./a"},
                "mcp_servers": {},
            }
            out = wizard._step5_write(
                mode="edit", name="preset", bot_cfg=bot_cfg,
                user_sources=[], bundled_dirs=[],
            )
            assert out == target
            # Owned files rewritten.
            assert (target / "config.yaml").is_file()
            # Sibling preserved.
            assert (target / ".env.example").read_text(encoding="utf-8") == "KEY=val"
            # Hand-written README preserved (wizard didn't stub over it).
            assert "Hand-written" in (target / "README.md").read_text(encoding="utf-8")
            # Stale skill wiped (skills are fully re-authored).
            assert not (target / "skills" / "deselected").exists()
            # No backup lingering on success.
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
                "agent": {"name": "Fresh", "trigger_phrase": "@operator", "tagline": "t"},
                "llm": {"provider": "anthropic", "model": "x", "system_prompt": "p"},
                "connector": {"browser_profile_dir": "./b", "auth_state_file": "./a"},
                "mcp_servers": {"notion": {"enabled": True, "command": "npx", "args": []}},
            }
            out = wizard._step5_write(
                mode="new", name="fresh", bot_cfg=bot_cfg,
                user_sources=[], bundled_dirs=[],
            )
            assert out.is_dir()
            assert (out / "config.yaml").is_file()
            assert (out / "portrait.txt").is_file()
            assert (out / "README.md").is_file()
            # skills.paths points at the local (non-existent) folder safely
            # — but since we passed no skills, skills.paths should be [].
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


# ── 8. Number-list parser ────────────────────────────────────────────────


def test_parse_number_list():
    """Accepts '1,3,5', rejects out-of-range and non-numeric."""
    assert wizard._parse_number_list("1,3,5", 5) == [0, 2, 4]
    assert wizard._parse_number_list("", 5) == []
    assert wizard._parse_number_list(" 2 ", 5) == [1]
    for bad in ("0", "6", "abc", "1,x"):
        try:
            wizard._parse_number_list(bad, 5)
            assert False, f"expected rejection for {bad!r}"
        except ValueError:
            pass
    print("  number-list parser: PASS")


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
    test_parse_number_list()
    print("\nAll tests passed.")
