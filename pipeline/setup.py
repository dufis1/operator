"""`operator setup` wizard — Phase 15.5.5.

Builds a new `agents/<name>/` bundle, or rewrites an existing one in place,
through a five-step guided TUI:

  1. Fighter select   — from-scratch (pm baseline, user names it) OR
                         preset (inherits name, edit-in-place).
  2. Power-ups (MCPs) — numbered-list `[x]`/`[ ]` toggle against each MCP
                         server block's `enabled` flag.
  3. Skills           — user-supplied paths (folder or single `.md` wrapped
                         in a stub folder) + base bot's bundled skills in
                         the same toggle pattern.
  4. API keys         — prompt for any `${VAR}` referenced by an enabled
                         MCP that isn't already in repo-root `.env`.
  5. Atomic write     — build bundle in a sibling tempdir, `os.rename` into
                         `agents/<name>/`. Edit-in-place first moves the
                         current bundle to `agents/<name>.bak-<ts>/`, then
                         swaps; `.bak` is deleted only on success.

All locked-in decisions are in `docs/plan.md`. The wizard never touches
runtime code paths; `config.py` simply filters `enabled: false` blocks at
load time, so the wizard just flips flags.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

import yaml
from rich.console import Console
from rich.prompt import Confirm, Prompt

from pipeline import face


_ROOT = Path(__file__).resolve().parents[1]
_AGENTS_DIR = _ROOT / "agents"
_ENV_FILE = _ROOT / ".env"
_PM_CONFIG = _AGENTS_DIR / "pm" / "config.yaml"

# Subcommand verbs the CLI reserves — a from-scratch bot can't use them as
# a name because `operator <reserved>` would never dispatch to the bot.
RESERVED_NAMES = {"setup", "list"}
# Lowercase start-with-letter, alphanumeric + dash/underscore, up to 32 chars.
# Matches the shape of existing bundled bots (pm, engineer, designer).
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")

# Env-var references inside MCP env blocks look like "${VAR_NAME}".
_ENV_REF_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")

console = Console()


# ── YAML dumper — keep multi-line strings readable (block literal "|") ────
#
# yaml.safe_dump's default folding mangles the `hints: |` blocks. Override
# the str representer so any string containing a newline round-trips as a
# block literal. Comments are still lost (we're going through dict form),
# but the hints stay readable for future hand-edits.
def _str_representer(dumper, data):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


yaml.add_representer(str, _str_representer, Dumper=yaml.SafeDumper)


class WizardCancel(Exception):
    """User aborted the wizard."""


# ── Small helpers ─────────────────────────────────────────────────────────


def _existing_bots() -> list[str]:
    if not _AGENTS_DIR.exists():
        return []
    return sorted(
        p.name for p in _AGENTS_DIR.iterdir()
        if p.is_dir() and (p / "config.yaml").is_file()
    )


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _dump_yaml(data: dict, path: Path) -> None:
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=1000),
        encoding="utf-8",
    )


def _validate_name(raw: str) -> tuple[bool, str]:
    """Return (ok, reason). Reason is empty on success."""
    name = raw.strip().lower()
    if not name:
        return False, "name cannot be empty"
    if not NAME_RE.match(name):
        return False, "use lowercase letters, digits, '-' or '_' (start with a letter)"
    if name in RESERVED_NAMES:
        return False, f"'{name}' is a reserved CLI subcommand"
    if name in _existing_bots():
        return False, f"agents/{name}/ already exists — pick a different name or re-run and choose 'preset'"
    return True, ""


def _prompt_name() -> str:
    """Loop until the user enters a valid, non-colliding name."""
    while True:
        raw = Prompt.ask("  [bold]name[/bold] (lowercase, short)").strip()
        ok, reason = _validate_name(raw)
        if ok:
            return raw.lower()
        console.print(f"  [red]✗ {reason}[/red]")


# ── Step 1 — fighter select ───────────────────────────────────────────────


def _step1_fighter_select() -> tuple[str, str, Path, dict]:
    """
    Returns
    -------
    mode : "new" | "edit"
    name : target bot name (also the target directory under agents/)
    base_dir : the agents/<x>/ directory whose bundled skills Step 3 will
               offer — pm in from-scratch mode, the preset in edit mode
    bot_cfg : parsed config.yaml dict ready to be mutated by later steps
    """
    console.print("[bold]1. Fighter select[/bold]")
    console.print("  (1) Start from scratch — pm baseline, you name it")
    console.print("  (2) Build on a preset  — pick an existing bot, edit in place")
    pick = Prompt.ask("  choose", choices=["1", "2"], default="1")

    if pick == "1":
        return _from_scratch()
    return _edit_preset()


def _from_scratch() -> tuple[str, str, Path, dict]:
    console.print()
    name = _prompt_name()
    display = Prompt.ask("  [bold]display name[/bold] (shown in the banner)", default=name.capitalize())
    trigger = Prompt.ask("  [bold]trigger phrase[/bold]", default="@operator")
    tagline = Prompt.ask("  [bold]tagline[/bold] (one line, under ~60 chars)", default="")
    user_display = Prompt.ask(
        "  [bold]your display name[/bold] (as it appears in Google Meet)",
        default="Your Name",
    )

    cfg = _load_yaml(_PM_CONFIG)
    cfg.setdefault("agent", {})
    cfg["agent"]["name"] = display
    cfg["agent"]["trigger_phrase"] = trigger
    cfg["agent"]["tagline"] = tagline
    cfg["agent"]["user_display_name"] = user_display

    # From-scratch baseline: every MCP block starts disabled. The user
    # flips on what they want in Step 2.
    for srv in cfg.get("mcp_servers", {}).values():
        srv["enabled"] = False

    # pm is the base for bundled skills.
    return "new", name, _AGENTS_DIR / "pm", cfg


def _edit_preset() -> tuple[str, str, Path, dict]:
    bots = _existing_bots()
    if not bots:
        console.print("  [red]No existing bots found — falling back to from-scratch.[/red]\n")
        return _from_scratch()

    console.print()
    for i, name in enumerate(bots, 1):
        tag = _bot_tagline(name)
        tag_str = f" — {tag}" if tag else ""
        console.print(f"  ({i}) {name}{tag_str}")
    choices = [str(i) for i in range(1, len(bots) + 1)]
    pick = Prompt.ask("  pick", choices=choices, default="1")
    name = bots[int(pick) - 1]

    cfg_path = _AGENTS_DIR / name / "config.yaml"
    cfg = _load_yaml(cfg_path)
    console.print(f"  [dim]editing agents/{name}/ in place[/dim]")
    return "edit", name, _AGENTS_DIR / name, cfg


def _bot_tagline(name: str) -> str:
    """Read tagline from agents/<name>/config.yaml."""
    cfg_path = _AGENTS_DIR / name / "config.yaml"
    try:
        return (_load_yaml(cfg_path).get("agent") or {}).get("tagline", "") or ""
    except Exception:
        return ""


# ── Step 2 — MCP toggle ───────────────────────────────────────────────────


def _step2_mcps(bot_cfg: dict) -> tuple[dict, set[str]]:
    """
    Toggle `enabled: true|false` on each MCP block via a numbered [x]/[ ]
    list. Returns the mutated bot_cfg plus the set of env-var names
    referenced by every now-enabled block (for Step 4).
    """
    console.print("\n[bold]2. Power-ups (MCPs)[/bold]")
    servers = bot_cfg.get("mcp_servers") or {}
    if not servers:
        console.print("  [dim]No MCP servers declared in the base config.[/dim]")
        return bot_cfg, set()

    names = list(servers.keys())
    while True:
        _render_toggle_list(names, [bool(servers[n].get("enabled", False)) for n in names])
        raw = Prompt.ask(
            "  toggle (comma-separated numbers, or Enter to accept)",
            default="",
        ).strip()
        if not raw:
            break
        try:
            picks = _parse_number_list(raw, len(names))
        except ValueError as e:
            console.print(f"  [red]✗ {e}[/red]")
            continue
        for idx in picks:
            n = names[idx]
            servers[n]["enabled"] = not bool(servers[n].get("enabled", False))

    # Collect env vars referenced by enabled servers.
    envs: set[str] = set()
    for n in names:
        if not servers[n].get("enabled"):
            continue
        for v in (servers[n].get("env") or {}).values():
            if isinstance(v, str):
                envs.update(_ENV_REF_RE.findall(v))

    return bot_cfg, envs


def _render_toggle_list(labels: list[str], checked: list[bool]) -> None:
    # `[x]` would be swallowed by Rich's markup parser (no such style), so
    # use a non-markup checked glyph. `[ ]` is safe — a space isn't a valid
    # style name so Rich prints it literally.
    for i, (label, is_on) in enumerate(zip(labels, checked), 1):
        mark = "[✓]" if is_on else "[ ]"
        console.print(f"  {i}. {mark} {label}")


def _parse_number_list(raw: str, n: int) -> list[int]:
    """Parse '1,3,5' → [0,2,4]. Rejects out-of-range or non-numeric entries."""
    picks = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if not token.isdigit():
            raise ValueError(f"'{token}' is not a number")
        idx = int(token) - 1
        if idx < 0 or idx >= n:
            raise ValueError(f"'{token}' is out of range (1–{n})")
        picks.append(idx)
    return picks


# ── Step 3 — Skills ───────────────────────────────────────────────────────


def _step3_skills(base_dir: Path, mode: str) -> tuple[list[Path], list[Path]]:
    """
    Returns
    -------
    user_sources : list of paths the user added (each is either a folder
                   with SKILL.md, or a single .md file the wizard will wrap)
    bundled_dirs : list of skill folders from base_dir/skills/ the user
                   elected to keep (all-checked default)
    """
    console.print("\n[bold]3. Skills[/bold]")

    # 3a — user's own paths
    user_sources: list[Path] = []
    default_user = Path.home() / ".claude" / "skills"
    default_hint = str(default_user) if default_user.is_dir() else ""

    console.print("  Add your own skills — enter a path to a folder with SKILL.md files,")
    console.print("  a single SKILL.md-style folder, or a single .md file.")
    console.print("  Blank input ends the list.")
    first_default = default_hint
    while True:
        prompt_default = first_default
        raw = Prompt.ask("    path", default=prompt_default).strip()
        first_default = ""  # only suggest ~/.claude/skills on the first iteration
        if not raw:
            break
        resolved = Path(os.path.expanduser(raw)).resolve()
        if not resolved.exists():
            console.print(f"    [red]✗ not found: {resolved}[/red]")
            continue
        if not _is_valid_skill_source(resolved):
            console.print(
                f"    [red]✗ {resolved} is not a SKILL.md folder, a parent of one, or a .md file[/red]"
            )
            continue
        user_sources.append(resolved)
        console.print(f"    [green]✓ added[/green] {resolved}")

    # 3b — bundled skills from the base bot
    bundled_dir = base_dir / "skills"
    bundled_dirs: list[Path] = []
    if bundled_dir.is_dir():
        candidates = sorted(
            p for p in bundled_dir.iterdir()
            if p.is_dir() and (p / "SKILL.md").is_file()
        )
        if candidates:
            console.print(f"\n  Bundled skills from agents/{base_dir.name}/skills/:")
            labels = [_skill_label(p) for p in candidates]
            checked = [True] * len(candidates)
            while True:
                _render_toggle_list(labels, checked)
                raw = Prompt.ask(
                    "    toggle (comma-separated numbers, or Enter to accept)",
                    default="",
                ).strip()
                if not raw:
                    break
                try:
                    picks = _parse_number_list(raw, len(candidates))
                except ValueError as e:
                    console.print(f"    [red]✗ {e}[/red]")
                    continue
                for idx in picks:
                    checked[idx] = not checked[idx]
            bundled_dirs = [p for p, keep in zip(candidates, checked) if keep]

    return user_sources, bundled_dirs


def _is_valid_skill_source(path: Path) -> bool:
    """True if `path` is a SKILL.md folder, a parent of one, or a .md file."""
    if path.is_file() and path.suffix.lower() == ".md":
        return True
    if path.is_dir():
        if (path / "SKILL.md").is_file():
            return True
        # parent-of-skill-folders layout (e.g. ~/.claude/skills)
        for child in path.iterdir():
            if child.is_dir() and (child / "SKILL.md").is_file():
                return True
    return False


def _skill_label(skill_dir: Path) -> str:
    """Label like 'my-skill — one-line description from frontmatter'."""
    md = skill_dir / "SKILL.md"
    try:
        text = md.read_text(encoding="utf-8")
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                fm = yaml.safe_load(parts[1]) or {}
                desc = (fm.get("description") or "").strip()
                if desc:
                    return f"{skill_dir.name} — {desc}"
    except Exception:
        pass
    return skill_dir.name


# ── Step 4 — API keys ─────────────────────────────────────────────────────


def _step4_api_keys(needed: set[str]) -> None:
    """Prompt for any ${VAR} referenced by an enabled MCP that isn't in .env.

    Appends new keys to repo-root `.env`. Never overwrites an existing key
    — if a value is already present, it's left alone and reported.
    """
    console.print("\n[bold]4. API keys[/bold]")
    if not needed:
        console.print("  [dim]Nothing to prompt for — no enabled MCP needs an env var.[/dim]")
        return

    existing = _parse_env(_ENV_FILE) if _ENV_FILE.exists() else {}
    missing = sorted(v for v in needed if not existing.get(v))

    if not missing:
        console.print("  [dim]All required keys are already present in .env.[/dim]")
        return

    console.print("  Enter a value for each key (leave blank to skip — MCP will fail at startup):")
    new_values: dict[str, str] = {}
    for var in missing:
        val = Prompt.ask(f"    {var}", default="").strip()
        if val:
            new_values[var] = val

    if not new_values:
        console.print("  [dim]No keys supplied — skipped.[/dim]")
        return

    _append_env(_ENV_FILE, new_values)
    console.print(f"  [green]✓ appended {len(new_values)} key(s) to .env[/green]")


def _parse_env(path: Path) -> dict[str, str]:
    """Lightweight parser — KEY=VALUE per line, ignores comments and blanks.

    Not a full .env parser (no quoting, no export/multiline) but matches the
    shape of the project's existing .env; good enough to detect presence.
    """
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out


def _append_env(path: Path, new_values: dict[str, str]) -> None:
    """Append keys to .env. Creates the file if it doesn't exist."""
    lines = []
    if path.exists():
        existing_text = path.read_text(encoding="utf-8")
        if existing_text and not existing_text.endswith("\n"):
            lines.append("")  # nudge the appended block onto a fresh line
    else:
        existing_text = ""
    lines.append("# added by operator setup")
    for k, v in new_values.items():
        # Single-quote values so shell-special chars don't need escaping.
        lines.append(f"{k}='{v}'")
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ── Step 5 — atomic write ─────────────────────────────────────────────────


def _step5_write(
    mode: str,
    name: str,
    bot_cfg: dict,
    user_sources: list[Path],
    bundled_dirs: list[Path],
) -> Path:
    """Build bundle in a sibling tempdir, then rename into place.

    Edit-in-place mode first moves the existing `agents/<name>/` to
    `agents/<name>.bak-<ts>/`, renames the new bundle into place, and only
    deletes the `.bak` once the swap succeeds. Any failure during build
    unwinds cleanly and leaves the existing bundle (and `.bak`) intact.
    """
    console.print("\n[bold]5. Writing bundle[/bold]")
    _AGENTS_DIR.mkdir(parents=True, exist_ok=True)

    # Tempdir on the same volume as _AGENTS_DIR so os.rename is atomic.
    tmp_parent = tempfile.mkdtemp(prefix=f".{name}.tmp-", dir=_AGENTS_DIR)
    tmp = Path(tmp_parent)

    target = _AGENTS_DIR / name
    backup: Path | None = None

    try:
        # Edit-in-place: seed the tempdir with the existing bundle so files
        # the wizard doesn't know about (.env.example, delegate scripts,
        # hand-written README, etc.) survive the rewrite. The wizard then
        # overwrites only the files it owns (config.yaml, portrait, skills/).
        if mode == "edit" and target.exists():
            shutil.copytree(target, tmp, dirs_exist_ok=True)
            # Skills are fully re-authored from user_sources + bundled_dirs —
            # wipe the existing skills/ so deselections actually drop folders.
            existing_skills = tmp / "skills"
            if existing_skills.exists():
                shutil.rmtree(existing_skills)

        # Copy skills into tmp/skills/.
        skills_dir = tmp / "skills"
        for src in user_sources:
            _copy_user_skill(src, skills_dir)
        for src in bundled_dirs:
            dst = skills_dir / src.name
            if dst.exists():
                # User-added skill with same folder name wins (already copied).
                continue
            shutil.copytree(src, dst)

        # Point the new bot at the local skills bundle only.
        bot_cfg.setdefault("skills", {})
        if skills_dir.exists():
            bot_cfg["skills"]["paths"] = [f"agents/{name}/skills"]
        else:
            bot_cfg["skills"]["paths"] = []
        bot_cfg["skills"].setdefault("progressive_disclosure", True)

        # config.yaml + portrait always (re)written; README stub only if the
        # bundle doesn't already have one (edit-in-place preserves a
        # hand-written README that got copied across from the seed).
        _dump_yaml(bot_cfg, tmp / "config.yaml")
        face.write_if_missing(name, tmp / "portrait.txt")
        readme = tmp / "README.md"
        if not readme.exists():
            _write_readme(readme, name, bot_cfg)

        # Swap into place.
        if mode == "edit" and target.exists():
            backup = _AGENTS_DIR / f"{name}.bak-{int(time.time())}"
            os.rename(target, backup)
        os.rename(tmp, target)
    except Exception:
        # Build or rename failed. Nothing in `target` has changed (we only
        # renamed it to `.bak` at the very end). If the `.bak` swap already
        # happened, restore it.
        shutil.rmtree(tmp, ignore_errors=True)
        if backup and backup.exists() and not target.exists():
            os.rename(backup, target)
        raise

    # Success: drop the backup if we made one.
    if backup and backup.exists():
        shutil.rmtree(backup, ignore_errors=True)

    console.print(f"  [green]✓ agents/{name}/[/green]")
    return target


def _copy_user_skill(src: Path, skills_root: Path) -> None:
    """Copy a user-supplied skill source into the new bundle.

    - Single .md file        → skills_root/<stem>/SKILL.md
    - Folder with SKILL.md   → skills_root/<folder-name>/
    - Parent of such folders → each child with SKILL.md copied through
    """
    skills_root.mkdir(parents=True, exist_ok=True)
    if src.is_file() and src.suffix.lower() == ".md":
        dst_dir = skills_root / src.stem
        dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst_dir / "SKILL.md")
        return
    if (src / "SKILL.md").is_file():
        shutil.copytree(src, skills_root / src.name, dirs_exist_ok=True)
        return
    # Parent folder — walk one level.
    for child in src.iterdir():
        if child.is_dir() and (child / "SKILL.md").is_file():
            shutil.copytree(child, skills_root / child.name, dirs_exist_ok=True)


def _write_readme(path: Path, name: str, bot_cfg: dict) -> None:
    tagline = (bot_cfg.get("agent") or {}).get("tagline", "") or ""
    display = (bot_cfg.get("agent") or {}).get("name", name)
    mcps = [k for k, v in (bot_cfg.get("mcp_servers") or {}).items() if v.get("enabled")]
    mcp_line = ", ".join(mcps) if mcps else "(none enabled)"

    body = (
        f"# {display}\n\n"
        f"{tagline}\n\n"
        f"Run: `operator {name}` or `operator {name} <meet-url>`.\n\n"
        f"MCPs: {mcp_line}\n\n"
        "## Note\n\n"
        "Skills and MCPs are independent in this bundle — enabling a skill\n"
        "that references an MCP tool doesn't auto-enable the MCP, and vice\n"
        "versa. If a skill asks for a tool that isn't wired, the model will\n"
        "either ask for it or degrade gracefully. Re-run `operator setup`\n"
        "and pick this bot as a preset to adjust either list.\n"
    )
    path.write_text(body, encoding="utf-8")


# ── Entry point ───────────────────────────────────────────────────────────


def run(argv: list[str]) -> int:
    """CLI entry. argv is ignored today; kept for future flags like --dry-run."""
    console.print()
    console.print("[bold cyan]Operator setup wizard[/bold cyan]")
    console.print("[dim]Five steps. Ctrl+C at any point cancels without writing.[/dim]\n")
    try:
        mode, name, base_dir, bot_cfg = _step1_fighter_select()
        bot_cfg, envs = _step2_mcps(bot_cfg)
        user_sources, bundled_dirs = _step3_skills(base_dir, mode)
        _step4_api_keys(envs)

        console.print()
        if not Confirm.ask(f"  Write bundle to agents/{name}/?", default=True):
            console.print("[yellow]Cancelled — nothing written.[/yellow]")
            return 1

        _step5_write(mode, name, bot_cfg, user_sources, bundled_dirs)
    except (KeyboardInterrupt, WizardCancel):
        console.print("\n[yellow]Cancelled.[/yellow]")
        return 1
    except Exception as e:
        console.print(f"\n[red]✗ setup failed: {e}[/red]")
        raise

    console.print(f"\n[bold green]Done.[/bold green] Try it: [bold]operator {name}[/bold]\n")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
