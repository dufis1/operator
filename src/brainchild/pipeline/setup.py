"""`brainchild setup` wizard — Phase 15.5.5.

Builds a new `agents/<name>/` bundle, or rewrites an existing one in place,
through a seven-step guided TUI:

  1. Fighter select    — arrow-key gallery: "Custom" + each existing bot,
                          right pane shows the highlighted bot's portrait.
                          Custom drops into name/display/trigger/tagline
                          text prompts; preset enters edit-in-place.
  2. Tools (MCPs)      — arrow-key multi-select against each MCP block's
                          `enabled` flag. Right pane = persistent build
                          card that updates live as the user toggles.
  3. Playbooks (Skills) — user-supplied paths (folder or single `.md`),
                          then arrow-key multi-select for the base bot's
                          bundled skills.
  4. Ground rules      — $EDITOR pops on a tempfile. Preset: inherit-with-
                          cursor or start blank. Custom: always blank.
  5. Personality       — same pattern as step 4.
  6. API keys          — prompt for any `${VAR}` referenced by an enabled
                          MCP that isn't already in repo-root `.env`.
  7. Atomic write +    — build bundle in a sibling tempdir, `os.rename`
     reveal              into `agents/<name>/`. Edit-in-place first moves
                          the current bundle to `agents/<name>.bak-<ts>/`,
                          then swaps; `.bak` is deleted only on success.
                          On success the final card re-renders with the
                          resolved real portrait — the gift to the user.

Ground rules and personality are the two halves of the bot's system prompt
(composed in `config.py` as personality first, ground_rules last). The
wizard treats them as separate steps so users author them as the two
distinct concerns they really are.

All locked-in decisions are in `docs/plan.md`. The wizard never touches
runtime code paths; `config.py` simply filters `enabled: false` blocks at
load time, so the wizard just flips flags.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from rich.align import Align
from rich.console import Console, Group, RenderableType
from rich.prompt import Confirm, Prompt
from rich.text import Text

from brainchild.pipeline import build_card, face
from brainchild.pipeline.picker import Choice, PickerCancelled, select_many, select_one


_ROOT = Path(__file__).resolve().parents[3]
_AGENTS_DIR = _ROOT / "agents"
_ENV_FILE = _ROOT / ".env"
_PM_CONFIG = _AGENTS_DIR / "pm" / "config.yaml"

# Subcommand verbs the CLI reserves — a from-scratch bot can't use them as
# a name because `brainchild <reserved>` would never dispatch to the bot.
RESERVED_NAMES = {"setup", "list", "try"}
# Lowercase start-with-letter, alphanumeric + dash/underscore, up to 32 chars.
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")

# Env-var references inside MCP env blocks look like "${VAR_NAME}".
_ENV_REF_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")

console = Console()


# ── YAML dumper — keep multi-line strings readable (block literal "|") ────
def _str_representer(dumper, data):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


yaml.add_representer(str, _str_representer, Dumper=yaml.SafeDumper)


class WizardCancel(Exception):
    """User aborted the wizard."""


# ── Wizard state — passed through every step ──────────────────────────────


@dataclass
class WizardState:
    """Mutable wizard state. Accumulates as the user moves through steps."""

    mode: str  # "new" | "edit"
    name: str  # bot name (also dir name under agents/)
    display_name: str
    tagline: str
    based_on: str  # baseline bot ("pm" for new, preset name for edit)
    portrait: str  # placeholder in custom mode, real portrait in edit mode
    bot_cfg: dict
    user_sources: list[Path] = field(default_factory=list)
    bundled_skill_dirs: list[Path] = field(default_factory=list)

    def equipped_mcps(self) -> list[str]:
        return [
            n for n, s in (self.bot_cfg.get("mcp_servers") or {}).items()
            if s.get("enabled")
        ]

    def equipped_skills(self) -> list[str]:
        return _resolve_user_skill_names(self.user_sources) + [
            d.name for d in self.bundled_skill_dirs
        ]

    def card(
        self,
        *,
        mcps: list[str] | None = None,
        skills: list[str] | None = None,
        title: str = "Your build",
        rainbow: bool = False,
    ) -> RenderableType:
        return build_card.render(
            name=self.display_name or self.name or "(unnamed)",
            tagline=self.tagline,
            portrait=self.portrait,
            power_ups=mcps if mcps is not None else self.equipped_mcps(),
            skills=skills if skills is not None else self.equipped_skills(),
            title=title,
            rainbow=rainbow,
        )


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
        return False, f"agents/{name}/ already exists — pick a different name or re-run and choose the preset"
    return True, ""


def _prompt_name() -> str:
    """Loop until the user enters a valid, non-colliding name."""
    while True:
        raw = Prompt.ask("  [bold]name[/bold] (lowercase, short)").strip()
        ok, reason = _validate_name(raw)
        if ok:
            return raw.lower()
        console.print(f"  ✗ {reason}")


def _bot_tagline(name: str) -> str:
    """Read tagline from agents/<name>/config.yaml."""
    cfg_path = _AGENTS_DIR / name / "config.yaml"
    try:
        return (_load_yaml(cfg_path).get("agent") or {}).get("tagline", "") or ""
    except Exception:
        return ""


# ── Step 1 — fighter select (arrow-key gallery) ───────────────────────────


def _step1_fighter_select() -> WizardState:
    console.print("[bold]1. Choose your base agent[/bold]\n")
    bots = _existing_bots()

    choices: list[Choice] = [
        Choice(
            label="Custom",
            sublabel="build from scratch",
            value="__custom__",
            preview=_custom_preview(),
        ),
    ]
    for bot in bots:
        tag = _bot_tagline(bot)
        choices.append(Choice(
            label=bot,
            sublabel=tag,
            value=bot,
            preview=_preset_preview(bot, tag),
        ))

    picked = select_one("", choices, console=console)
    console.print()
    if picked.value == "__custom__":
        return _from_scratch()
    return _edit_preset(picked.value)


def _custom_preview() -> RenderableType:
    return Group(
        Align.center(Text(build_card.PLACEHOLDER_PORTRAIT, style="bold")),
        Text(""),
        Align.center(Text("Custom", style="bold")),
        Align.center(Text("Build from scratch.", style="dim")),
    )


def _preset_preview(name: str, tagline: str) -> RenderableType:
    portrait_path = _AGENTS_DIR / name / "portrait.txt"
    portrait = face.load_or_render(name, portrait_path)
    return Group(
        Align.center(Text(portrait, style="bold")),
        Text(""),
        Align.center(Text(name, style="bold")),
        Align.center(Text(tagline or "(no tagline)", style="dim")),
    )


def _from_scratch() -> WizardState:
    name = _prompt_name()
    tagline = Prompt.ask("  [bold]tagline[/bold]", default="", show_default=False)
    display = name
    trigger = f"@{name}"

    cfg = _load_yaml(_PM_CONFIG)
    cfg.setdefault("agent", {})
    cfg["agent"]["name"] = display
    cfg["agent"]["trigger_phrase"] = trigger
    cfg["agent"]["tagline"] = tagline

    # From-scratch baseline: every MCP block starts disabled. The user
    # flips on what they want in Step 2.
    for srv in cfg.get("mcp_servers", {}).values():
        srv["enabled"] = False

    # From-scratch also starts with blank personality + ground_rules — the
    # user authors both in steps 4 and 5 (or leaves blank if they want).
    cfg["personality"] = ""
    cfg["ground_rules"] = ""

    return WizardState(
        mode="new",
        name=name,
        display_name=display,
        tagline=tagline,
        based_on="pm",
        portrait=build_card.PLACEHOLDER_PORTRAIT,
        bot_cfg=cfg,
    )


def _edit_preset(name: str) -> WizardState:
    cfg_path = _AGENTS_DIR / name / "config.yaml"
    cfg = _load_yaml(cfg_path)
    portrait_path = _AGENTS_DIR / name / "portrait.txt"
    portrait = face.load_or_render(name, portrait_path)
    agent = cfg.get("agent") or {}
    console.print(f"  [dim]editing agents/{name}/ in place[/dim]")
    return WizardState(
        mode="edit",
        name=name,
        display_name=agent.get("name", name),
        tagline=agent.get("tagline", "") or "",
        based_on=name,
        portrait=portrait,
        bot_cfg=cfg,
    )


def _base_dir(state: WizardState) -> Path:
    """Where bundled skills come from in step 3 — pm baseline for new bots,
    the preset itself for edit-in-place."""
    return _AGENTS_DIR / ("pm" if state.mode == "new" else state.name)


# ── Step 2 — MCP toggle (arrow-key multi-select with build card) ──────────


def _step2_mcps(state: WizardState) -> None:
    """Mutates state.bot_cfg['mcp_servers'][*]['enabled'] in place."""
    console.print("\n[bold]2. Tools (MCPs)[/bold]")
    console.print("  [dim]External systems your bot can reach — GitHub, Linear, Figma, etc.[/dim]")
    console.print("  [dim]Toggle with SPACE, confirm with ENTER.[/dim]\n")
    servers = state.bot_cfg.get("mcp_servers") or {}
    if not servers:
        console.print("  [dim]No MCP servers declared in the base config.[/dim]")
        return

    names = list(servers.keys())
    choices = [
        Choice(label=n, sublabel=(servers[n].get("description") or "").strip())
        for n in names
    ]
    initial = [bool(servers[n].get("enabled", False)) for n in names]

    def right_pane(_cursor, checked):
        enabled = [names[i] for i, on in enumerate(checked or []) if on]
        return state.card(mcps=enabled)

    final = select_many(
        "",
        choices,
        initial_checked=initial,
        right_pane=right_pane,
        console=console,
    )
    for i, n in enumerate(names):
        servers[n]["enabled"] = bool(final[i])


# ── Step 3 — Skills ───────────────────────────────────────────────────────


def _step3_skills(state: WizardState, base_dir: Path) -> None:
    """Mutates state.user_sources and state.bundled_skill_dirs in place.

    Bundled-skills picker first (from pm for new bots, preset itself for edit),
    then a plain y/n for adding the user's own skills. If yes, path-input loop.
    """
    console.print("[bold]3. Playbooks (Skills)[/bold]")
    console.print("  [dim]Specific procedures your bot knows — often compose the tools above.[/dim]\n")
    console.print(f"  Your [bold]{state.name}[/bold] agent comes with the following:\n")

    bundled_dir = base_dir / "skills"
    candidates: list[Path] = []
    if bundled_dir.is_dir():
        candidates = sorted(
            p for p in bundled_dir.iterdir()
            if p.is_dir() and (p / "SKILL.md").is_file()
        )

    if candidates:
        choices = [Choice(label=p.name, sublabel=_skill_subtitle(p)) for p in candidates]
        initial = [True] * len(candidates)

        def right_pane(_cursor, checked):
            bundled_now = [candidates[i].name for i, on in enumerate(checked or []) if on]
            return state.card(skills=bundled_now)

        final = select_many(
            "",
            choices,
            initial_checked=initial,
            right_pane=right_pane,
            console=console,
        )
        state.bundled_skill_dirs = [p for p, keep in zip(candidates, final) if keep]

    console.print()
    if not Confirm.ask("  Add your own skills?", default=False):
        return

    console.print()
    while True:
        raw = Prompt.ask(
            "  Enter path to your SKILLS folder or single .md file",
            default="",
            show_default=False,
        ).strip()
        if not raw:
            break
        resolved = Path(os.path.expanduser(raw)).resolve()
        if not resolved.exists():
            console.print(f"    [red]✗ not found: {resolved}[/red]")
            continue
        if not _is_valid_skill_source(resolved):
            console.print(
                f"    ✗ {resolved} is not a SKILL.md folder, a parent of one, or a .md file"
            )
            continue
        state.user_sources.append(resolved)
        console.print(f"    ✓ added {resolved}")


def _is_valid_skill_source(path: Path) -> bool:
    """True if `path` is a SKILL.md folder, a parent of one, or a .md file."""
    if path.is_file() and path.suffix.lower() == ".md":
        return True
    if path.is_dir():
        if (path / "SKILL.md").is_file():
            return True
        for child in path.iterdir():
            if child.is_dir() and (child / "SKILL.md").is_file():
                return True
    return False


def _resolve_user_skill_names(sources: list[Path]) -> list[str]:
    """Map user-supplied paths to the skill folder names they'll create
    inside the new bundle's skills/ directory."""
    names: list[str] = []
    for src in sources:
        if src.is_file() and src.suffix.lower() == ".md":
            names.append(src.stem)
            continue
        if (src / "SKILL.md").is_file():
            names.append(src.name)
            continue
        if src.is_dir():
            for child in src.iterdir():
                if child.is_dir() and (child / "SKILL.md").is_file():
                    names.append(child.name)
    return names


def _skill_subtitle(skill_dir: Path) -> str:
    """Description from SKILL.md frontmatter. Uncapped — long descriptions
    wrap naturally in the picker's per-choice sublabel line."""
    md = skill_dir / "SKILL.md"
    try:
        text = md.read_text(encoding="utf-8")
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                fm = yaml.safe_load(parts[1]) or {}
                desc = (fm.get("description") or "").strip()
                if desc:
                    return desc
    except Exception:
        pass
    return ""


# ── Step 4 — Ground rules ─────────────────────────────────────────────────


def _step4_ground_rules(state: WizardState) -> None:
    """Edit the bot's always-true constraints via $EDITOR."""
    console.print("[bold]4. Ground rules[/bold]")
    console.print("  [dim]Always-true constraints — what the bot should do or avoid on every turn.[/dim]")
    console.print("  [dim]Land last in the composed system prompt, so the model weights them heavily.[/dim]\n")
    _edit_prompt_block(state, key="ground_rules", label="ground rules")


# ── Step 5 — Personality ──────────────────────────────────────────────────


def _step5_personality(state: WizardState) -> None:
    """Edit the bot's voice/tone via $EDITOR."""
    console.print("[bold]5. Personality[/bold]")
    console.print("  [dim]Who the bot is — voice, tone, how it shows up in chat.[/dim]")
    console.print("  [dim]Leads the composed system prompt.[/dim]\n")
    _edit_prompt_block(state, key="personality", label="personality")


def _edit_prompt_block(state: WizardState, *, key: str, label: str) -> None:
    """Author a multi-line prompt block (ground_rules or personality).

    From-scratch: blank $EDITOR pop — user can leave empty and nothing breaks.
    Edit-preset: two-path — (a) inherit existing text and keep typing, or
    (b) start from a blank editor.
    """
    current = (state.bot_cfg.get(key) or "").strip()

    if state.mode == "new" or not current:
        console.print(f"  [dim]Blank by default — leave empty if you don't want to customize.[/dim]")
        console.print(f"  [dim]Your $EDITOR will open; save and exit when done.[/dim]\n")
        input("  Press Enter to open editor… ")
        new_text = _edit_in_editor(seed="", label=label)
    else:
        choices = [
            Choice(
                label=f"Inherit {state.name}'s {label} — edit with cursor",
                sublabel=f"{_first_line(current)} …",
                value="keep",
            ),
            Choice(
                label="Start blank",
                sublabel="write your own from scratch (or leave empty)",
                value="blank",
            ),
        ]
        picked = select_one("", choices, console=console)
        console.print()
        seed = current if picked.value == "keep" else ""
        new_text = _edit_in_editor(seed=seed, label=label)

    state.bot_cfg[key] = new_text
    if new_text:
        console.print(f"  ✓ {label} saved ({len(new_text)} chars)")
    else:
        console.print(f"  [dim]{label} left blank[/dim]")


def _first_line(text: str, *, max_chars: int = 60) -> str:
    """Best-effort preview of a multi-line block for the picker sublabel."""
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if len(first) > max_chars:
        return first[: max_chars - 1].rstrip() + "…"
    return first


def _edit_in_editor(*, seed: str, label: str) -> str:
    """Pop $EDITOR on a tempfile containing `seed`. Return stripped final content.

    Falls back to `vi` if neither $EDITOR nor $VISUAL is set.
    """
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    fd, tmp_path = tempfile.mkstemp(prefix=f"brainchild-{label.replace(' ', '_')}-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(seed)
        subprocess.call([editor, tmp_path])
        with open(tmp_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ── Step 6 — API keys ─────────────────────────────────────────────────────


def _step6_api_keys(needed: set[str]) -> None:
    console.print("\n[bold]6. API keys[/bold]")
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
    console.print(f"  ✓ appended {len(new_values)} key(s) to .env")


def _parse_env(path: Path) -> dict[str, str]:
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
    lines = []
    if path.exists():
        existing_text = path.read_text(encoding="utf-8")
        if existing_text and not existing_text.endswith("\n"):
            lines.append("")
    else:
        existing_text = ""
    lines.append("# added by brainchild setup")
    for k, v in new_values.items():
        lines.append(f"{k}='{v}'")
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ── Step 7 — atomic write ─────────────────────────────────────────────────


def _step7_write(state: WizardState) -> Path:
    """Build bundle in a sibling tempdir, then rename into place.

    Edit-in-place mode first moves the existing `agents/<name>/` to
    `agents/<name>.bak-<ts>/`, renames the new bundle into place, and only
    deletes the `.bak` once the swap succeeds.
    """
    console.print("\n[bold]7. Writing bundle[/bold]")
    _AGENTS_DIR.mkdir(parents=True, exist_ok=True)

    tmp_parent = tempfile.mkdtemp(prefix=f".{state.name}.tmp-", dir=_AGENTS_DIR)
    tmp = Path(tmp_parent)

    target = _AGENTS_DIR / state.name
    backup: Path | None = None

    try:
        if state.mode == "edit" and target.exists():
            shutil.copytree(target, tmp, dirs_exist_ok=True)
            existing_skills = tmp / "skills"
            if existing_skills.exists():
                shutil.rmtree(existing_skills)

        skills_dir = tmp / "skills"
        for src in state.user_sources:
            _copy_user_skill(src, skills_dir)
        for src in state.bundled_skill_dirs:
            dst = skills_dir / src.name
            if dst.exists():
                continue
            shutil.copytree(src, dst)

        state.bot_cfg.setdefault("skills", {})
        if skills_dir.exists():
            state.bot_cfg["skills"]["paths"] = [f"agents/{state.name}/skills"]
        else:
            state.bot_cfg["skills"]["paths"] = []
        state.bot_cfg["skills"].setdefault("progressive_disclosure", True)

        _dump_yaml(state.bot_cfg, tmp / "config.yaml")
        face.write_if_missing(state.name, tmp / "portrait.txt")
        readme = tmp / "README.md"
        if not readme.exists():
            _write_readme(readme, state.name, state.bot_cfg)

        if state.mode == "edit" and target.exists():
            backup = _AGENTS_DIR / f"{state.name}.bak-{int(time.time())}"
            os.rename(target, backup)
        os.rename(tmp, target)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        if backup and backup.exists() and not target.exists():
            os.rename(backup, target)
        raise

    if backup and backup.exists():
        shutil.rmtree(backup, ignore_errors=True)

    console.print(f"  ✓ agents/{state.name}/")
    return target


def _copy_user_skill(src: Path, skills_root: Path) -> None:
    """Copy a user-supplied skill source into the new bundle."""
    skills_root.mkdir(parents=True, exist_ok=True)
    if src.is_file() and src.suffix.lower() == ".md":
        dst_dir = skills_root / src.stem
        dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst_dir / "SKILL.md")
        return
    if (src / "SKILL.md").is_file():
        shutil.copytree(src, skills_root / src.name, dirs_exist_ok=True)
        return
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
        f"Run: `brainchild {name}` or `brainchild {name} <meet-url>`.\n\n"
        f"MCPs: {mcp_line}\n\n"
        "## Note\n\n"
        "Skills and MCPs are independent in this bundle — enabling a skill\n"
        "that references an MCP tool doesn't auto-enable the MCP, and vice\n"
        "versa. If a skill asks for a tool that isn't wired, the model will\n"
        "either ask for it or degrade gracefully. Re-run `brainchild setup`\n"
        "and pick this bot as a preset to adjust either list.\n"
    )
    path.write_text(body, encoding="utf-8")


# ── Reveal ────────────────────────────────────────────────────────────────


def _reveal(state: WizardState) -> None:
    """Final card render — placeholder portrait swaps for the real one."""
    state.portrait = face.load_or_render(
        state.name, _AGENTS_DIR / state.name / "portrait.txt",
    )
    console.print()
    console.print("[bold]✨ bot reveal 🎁[/bold]")
    console.print(state.card(title=f"Meet {state.name}", rainbow=True))


# ── Entry point ───────────────────────────────────────────────────────────


def run(argv: list[str]) -> int:
    """CLI entry. argv is ignored today; kept for future flags like --dry-run."""
    console.print()
    console.print("[bold]Brainchild setup wizard[/bold]")
    console.print("[dim]Seven steps. Ctrl+C / q at any picker cancels without writing.[/dim]\n")
    try:
        state = _step1_fighter_select()

        console.clear()
        _step2_mcps(state)

        console.clear()
        _step3_skills(state, _base_dir(state))

        console.clear()
        _step4_ground_rules(state)

        console.clear()
        _step5_personality(state)

        console.clear()
        envs = _collect_env_refs(state)
        _step6_api_keys(envs)

        console.print()
        console.input("  [bold]Press Enter to reveal your bot ✨🎁[/bold] ")

        console.clear()
        _step7_write(state)
        _reveal(state)
    except (KeyboardInterrupt, PickerCancelled, WizardCancel):
        console.print("\nCancelled.")
        return 1
    except Exception as e:
        console.print(f"\n✗ setup failed: {e}")
        raise

    console.print(f"\n[bold]Done.[/bold] Try it: [bold]brainchild {state.name}[/bold]\n")
    return 0


def _collect_env_refs(state: WizardState) -> set[str]:
    """Re-derive env refs from state's currently-enabled MCPs."""
    envs: set[str] = set()
    servers = state.bot_cfg.get("mcp_servers") or {}
    for n, srv in servers.items():
        if not srv.get("enabled"):
            continue
        for v in (srv.get("env") or {}).values():
            if isinstance(v, str):
                envs.update(_ENV_REF_RE.findall(v))
    return envs


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
