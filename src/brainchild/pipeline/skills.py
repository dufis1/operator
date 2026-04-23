"""
Skill loader — reads Claude Code-style SKILL.md folders for Brainchild.

Each skill lives in a folder with a `SKILL.md` file whose YAML frontmatter
declares:

  - `name` — required. Unique identifier shown in the wizard + LLM prompts.
  - `description` — required. Trigger-phrase-first one-liner the LLM matches
    against. Lead with the phrases that should fire the skill.
  - `allowed-tools` — optional list/csv. Non-MCP tool hints; anything outside
    SUPPORTED_ALLOWED_TOOLS logs a WARN but still loads.
  - `mcp-required` (alias `mcp_required`) — optional list/csv of MCP server
    names this skill fundamentally depends on. Consumed by the setup wizard
    to lock the matching MCP toggles on (you can't disable an MCP a chosen
    skill needs — remove the skill first). Missing = no declared deps
    (honest default). User-authored skills that omit this field load
    unconditionally; the runtime safety net in mcp_client.execute_tool
    raises an actionable "server disabled" error if the LLM actually tries
    to call a tool from a disabled server.

The remainder of the file is the skill body — free-form instructions fed to
the LLM when the skill is invoked.

Two path shapes are supported in `config.SKILLS_PATHS`:
  - A folder containing SKILL.md  → single skill.
  - A parent folder                → scanned one level deep for */SKILL.md.

Malformed or missing entries WARN and are skipped rather than crashing.
Duplicate names across paths resolve last-wins so users can order their
`skills:` list broader → more specific to layer overrides.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

# MCP-tool-aware subset. We WARN on anything outside this (e.g. Claude Code's
# Bash/Edit/Write/Read) because the meeting bot can't honour those — the skill
# still loads; the model may decline to use it.
SUPPORTED_ALLOWED_TOOLS = {"load_skill"}


@dataclass
class Skill:
    name: str
    description: str
    body: str
    allowed_tools: list[str] = field(default_factory=list)
    # MCP servers whose tools this skill fundamentally relies on. Consumed by
    # the setup wizard to preseed enabled=true on the MCP step and to grey out
    # toggles the user can't safely disable. Missing field → empty list → no
    # declared deps (honest default; runtime falls back on the granular
    # "disabled server" error in mcp_client.disabled_server_for_tool).
    mcp_required: list[str] = field(default_factory=list)
    source_path: str = ""


def _parse_skill_md(path: Path) -> Skill | None:
    """Parse a SKILL.md file. Returns None on any failure (with WARN logged)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        log.warning(f"SKILLS: could not read {path}: {e}")
        return None

    if not text.startswith("---"):
        log.warning(f"SKILLS: {path} missing frontmatter — skipping")
        return None

    # Split frontmatter out of the body.
    parts = text.split("---", 2)
    if len(parts) < 3:
        log.warning(f"SKILLS: {path} has unterminated frontmatter — skipping")
        return None
    _, fm_text, body = parts

    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as e:
        log.warning(f"SKILLS: {path} has malformed YAML frontmatter: {e} — skipping")
        return None

    if not isinstance(fm, dict):
        log.warning(f"SKILLS: {path} frontmatter is not a mapping — skipping")
        return None

    name = (fm.get("name") or "").strip()
    description = (fm.get("description") or "").strip()
    if not name or not description:
        log.warning(f"SKILLS: {path} missing name or description — skipping")
        return None

    raw_tools = fm.get("allowed-tools") or []
    if isinstance(raw_tools, str):
        allowed = [t.strip() for t in raw_tools.split(",") if t.strip()]
    elif isinstance(raw_tools, list):
        allowed = [str(t).strip() for t in raw_tools if str(t).strip()]
    else:
        log.warning(f"SKILLS: {path} allowed-tools has unexpected type — ignoring")
        allowed = []

    unsupported = [t for t in allowed if t not in SUPPORTED_ALLOWED_TOOLS]
    if unsupported:
        log.warning(
            f"SKILLS: {path} declares unsupported allowed-tools {unsupported} — "
            f"loading anyway; model may decline to use them"
        )

    raw_required = fm.get("mcp-required") or fm.get("mcp_required") or []
    if isinstance(raw_required, str):
        mcp_required = [t.strip() for t in raw_required.split(",") if t.strip()]
    elif isinstance(raw_required, list):
        mcp_required = [str(t).strip() for t in raw_required if str(t).strip()]
    else:
        log.warning(f"SKILLS: {path} mcp-required has unexpected type — ignoring")
        mcp_required = []

    return Skill(
        name=name,
        description=description,
        body=body.strip(),
        allowed_tools=allowed,
        mcp_required=mcp_required,
        source_path=str(path),
    )


def _resolve_entry(entry: str) -> list[Path]:
    """Resolve one config entry to a list of SKILL.md file paths."""
    root = Path(os.path.expanduser(entry)).resolve()
    if not root.exists() or not root.is_dir():
        log.warning(f"SKILLS: path not found or not a directory: {entry} — skipping")
        return []

    direct = root / "SKILL.md"
    if direct.is_file():
        return [direct]

    children = sorted(
        p for p in root.iterdir()
        if p.is_dir() and (p / "SKILL.md").is_file()
    )
    if not children:
        log.warning(f"SKILLS: no SKILL.md found under {entry} — skipping")
        return []
    return [c / "SKILL.md" for c in children]


def load_skills(paths: list[str]) -> list[Skill]:
    """Load all skills declared in config. Duplicates resolve last-wins."""
    if not paths:
        return []

    by_name: dict[str, Skill] = {}
    attempted = 0

    for entry in paths:
        for md in _resolve_entry(entry):
            attempted += 1
            skill = _parse_skill_md(md)
            if not skill:
                continue
            if skill.name in by_name:
                prev = by_name[skill.name].source_path
                log.warning(
                    f"SKILLS: duplicate name {skill.name!r} — "
                    f"{skill.source_path} overrides {prev} (last-wins)"
                )
            by_name[skill.name] = skill

    skills = list(by_name.values())
    _log_banner(skills, attempted)
    return skills


def _log_banner(skills: list[Skill], attempted: int):
    """Mirror the MCP startup banner shape."""
    loaded = len(skills)
    if attempted == 0:
        log.info("SKILLS: no skill paths configured")
        return
    names = ", ".join(f"{s.name} ✓" for s in skills)
    log.info(f"SKILLS: {loaded}/{attempted} loaded ({names})")
