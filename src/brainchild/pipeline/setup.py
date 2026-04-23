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
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from rich.align import Align
from rich.console import Console, Group, RenderableType
from rich.prompt import Prompt
from rich.text import Text

from brainchild.pipeline import build_card, face
from brainchild.pipeline.auth import run_auth
from brainchild.pipeline.claude_code_import import (
    append_env_placeholders,
    claude_code_installed_and_logged_in,
    discover_all_mcps,
    read_user_claude_md,
)
from brainchild.pipeline.picker import Choice, PickerCancelled, select_many, select_one
from brainchild.pipeline.readiness import STATUS_GLYPH, report_mcp_readiness
from brainchild.pipeline.skills import _parse_skill_md


_ROOT = Path(__file__).resolve().parents[3]
_AGENTS_DIR = Path.home() / ".brainchild" / "agents"
_BUNDLED_AGENTS_DIR = Path(__file__).resolve().parents[1] / "agents"
_ENV_FILE = _ROOT / ".env"
_PM_CONFIG = _BUNDLED_AGENTS_DIR / "pm" / "config.yaml"

# Subcommand verbs the CLI reserves — a from-scratch bot can't use them as
# a name because `brainchild <reserved>` would never dispatch to the bot.
RESERVED_NAMES = {"setup", "list", "try"}
# Lowercase start-with-letter, alphanumeric + dash/underscore, up to 32 chars.
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")

# Env-var references inside MCP env blocks look like "${VAR_NAME}".
_ENV_REF_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")

# First-party MCP servers — labeled "(official)" in the step 2 picker so
# users know which are trustworthy out of the gate. figma is GLips community;
# claude-code is Brainchild's own.
_OFFICIAL_MCPS = {"github", "linear", "notion"}

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
    """Mutable wizard state. Accumulates as the user moves through steps.

    Skill selection (Phase 15.11): `enabled_skill_names` is the single
    source of truth for which skills the bot will activate. It's the list
    written out to config.yaml under `skills.enabled`. External paths
    from which skills are discovered live on `bot_cfg["skills"]["external_paths"]`
    and are edited in-place by the skills step.
    """

    mode: str  # "new" | "edit"
    name: str  # bot name (also dir name under agents/)
    display_name: str
    tagline: str
    based_on: str  # baseline bot ("pm" for new, preset name for edit)
    portrait: str  # placeholder in custom mode, real portrait in edit mode
    bot_cfg: dict
    enabled_skill_names: list[str] = field(default_factory=list)

    def equipped_mcps(self) -> list[str]:
        return [
            n for n, s in (self.bot_cfg.get("mcp_servers") or {}).items()
            if s.get("enabled")
        ]

    def equipped_skills(self) -> list[str]:
        return list(self.enabled_skill_names)

    def card(
        self,
        *,
        mcps: list[str] | None = None,
        skills: list[str] | None = None,
        title: str = "Your build",
    ) -> RenderableType:
        return build_card.render(
            name=self.display_name or self.name or "(unnamed)",
            tagline=self.tagline,
            portrait=self.portrait,
            power_ups=mcps if mcps is not None else self.equipped_mcps(),
            skills=skills if skills is not None else self.equipped_skills(),
            title=title,
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
            label="custom",
            value="__custom__",
            preview=_custom_preview(),
        ),
    ]
    for bot in bots:
        tag = _bot_tagline(bot)
        choices.append(Choice(
            label=bot,
            value=bot,
            preview=_preset_preview(bot, tag),
        ))

    picked = select_one("", choices, console=console)
    console.print()
    if picked.value == "__custom__":
        return _from_scratch()
    if picked.value == "claude":
        # claude preset is a hard dependency on the Claude Code CLI — the
        # whole agent identity is "inherit the user's claude-code setup."
        # Gate selection at the picker so the user fixes the prereq first
        # rather than discovering it mid-wizard. Option (b) from the plan:
        # show the preset, block with hint, send back to the gallery.
        ok, reason = claude_code_installed_and_logged_in()
        if not ok:
            console.print(f"  [red]✗ claude preset requires Claude Code:[/red] {reason}")
            console.print(
                "  [dim]Install Claude Code (https://claude.ai/code) and run "
                "`claude login`, then re-run `brainchild setup`.[/dim]\n"
            )
            return _step1_fighter_select()
    return _edit_preset(picked.value)


def _custom_preview() -> RenderableType:
    return Group(
        Align.center(Text(build_card.PLACEHOLDER_PORTRAIT, style="bold")),
        Text(""),
        Align.center(Text("custom", style="bold")),
        Align.center(Text("build from scratch", style="dim")),
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

    # Personality + ground_rules carry pm's defaults through — steps 4/5
    # offer approve-or-start-blank so users can inherit or replace.

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
    state = WizardState(
        mode="edit",
        name=name,
        display_name=agent.get("name", name),
        tagline=agent.get("tagline", "") or "",
        based_on=name,
        portrait=portrait,
        bot_cfg=cfg,
    )
    if name == "claude":
        _auto_import_claude_setup(state)
    return state


def _auto_import_claude_setup(state: WizardState) -> None:
    """Discover the user's Claude Code MCPs + CLAUDE.md and fold them into
    state so the rest of the wizard just works. Only runs for the `claude`
    preset. Idempotent — re-running the wizard won't duplicate MCP blocks
    (collisions keep the existing entry so user curation sticks).

    Skills: NOT imported via this path — the claude preset's bundled
    config.yaml ships with `external_paths: [~/.claude/skills]`, so
    step 3's picker scans that path live and lets the user toggle which
    skills to enable. No copy, no one-shot import: edits to
    ~/.claude/skills propagate on next meeting join.

    CLAUDE.md content is stashed on state for step 4 to optionally append
    to ground_rules.
    """
    servers = state.bot_cfg.setdefault("mcp_servers", {})

    mcps, wrapped = discover_all_mcps()
    added_mcps: list[str] = []
    for m in mcps:
        if m.name in servers:
            continue
        servers[m.name] = m.block
        added_mcps.append(m.name)

    claude_md = read_user_claude_md()
    # Stash on state via a plain attribute — WizardState is a dataclass
    # but Python still permits ad-hoc attrs. Step 4 reads it.
    state._claude_md_content = claude_md  # type: ignore[attr-defined]

    console.print()
    console.print("  [bold]Claude Code auto-import:[/bold]")
    if added_mcps:
        console.print(
            f"    [green]✓[/green] {len(added_mcps)} MCP(s) imported"
            f"{f' ({wrapped} hosted, wrapped via mcp-remote)' if wrapped else ''}: "
            f"{', '.join(added_mcps)}"
        )
    else:
        console.print("    [dim]No new MCPs to import (already present or none configured).[/dim]")
    console.print(
        "    [dim]Skills at ~/.claude/skills/ are available via external_paths — "
        "toggle them in the next step.[/dim]"
    )
    if claude_md:
        console.print(
            f"    [dim]Found ~/.claude/CLAUDE.md ({len(claude_md)} chars) — "
            f"step 4 will offer to append it to ground rules.[/dim]"
        )


# ── Step 2 — MCP toggle (arrow-key multi-select with build card) ──────────


def _step2_mcps(state: WizardState) -> None:
    """Mutates state.bot_cfg['mcp_servers'][*]['enabled'] in place.

    Runs AFTER the skills step (see run()) so we can lock MCPs that the
    user's chosen skills declared via `mcp-required`. Locked rows preseed to
    enabled=true and can't be toggled off — to disable the MCP the user
    must first remove the skill that requires it.
    """
    console.print("\n[bold]3. MCPs[/bold]\n")
    servers = state.bot_cfg.get("mcp_servers") or {}
    if not servers:
        console.print("  [dim]No MCP servers declared in the base config.[/dim]")
        return

    required_map = _required_mcps_from_skills(state)

    # Warn (not fail) if a skill declared a dep the preset doesn't scaffold —
    # typically a user-authored skill added to a bundle that didn't include
    # that MCP. The run still proceeds; the skill will hit the granular
    # "server disabled" error (test_916) at tool-call time.
    unscaffolded = {s: ss for s, ss in required_map.items() if s not in servers}
    if unscaffolded:
        for server, skill_names in unscaffolded.items():
            console.print(
                f"  [yellow]⚠[/yellow] skill(s) {', '.join(skill_names)} declare "
                f"[bold]{server}[/bold] as required, but this agent doesn't have "
                f"{server} configured — add it manually to mcp_servers in "
                f"config.yaml or remove the skill."
            )
        console.print()

    # Sort: officials first (alphabetical), then other third-party, claude-code
    # always last — trust signal reads top-down.
    names = sorted(servers.keys(), key=_mcp_sort_key)
    choices = []
    initial = []
    for n in names:
        locked_skills = required_map.get(n, [])
        choices.append(_mcp_choice(n, locked_by=locked_skills))
        # Preseed required rows to enabled=true even if the scaffolded default
        # had enabled=false; the picker enforces the lock but we still feed
        # the truth so the right-pane card reflects it on first render.
        initial.append(True if locked_skills else bool(servers[n].get("enabled", False)))

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

    # Claude preset: append commented env-var placeholders for any MCP the
    # user just approved, so step 5 has a ready list to prompt for and
    # later `brainchild run claude` gets a clear "set X in .env" from the
    # preflight instead of silent boot failures. Idempotent — vars already
    # set or placeheld are skipped.
    if state.based_on == "claude":
        needed: set[str] = set()
        for n in names:
            if not servers[n].get("enabled"):
                continue
            for v in (servers[n].get("env") or {}).values():
                if isinstance(v, str):
                    needed.update(_ENV_REF_RE.findall(v))
        if needed:
            added = append_env_placeholders(sorted(needed), _ENV_FILE)
            if added:
                console.print(
                    f"  [dim]+ appended {len(added)} env-var placeholder(s) to "
                    f".env: {', '.join(added)}[/dim]"
                )

    _render_mcp_readiness(servers)


def _render_mcp_readiness(servers: dict) -> None:
    """Show ✓/⚠/✗ per enabled MCP, and offer inline auth for OAuth gaps.

    Skipped silently when nothing is enabled. For env servers that are
    missing vars, we just show the glyph + hint — step 5 (API keys) is
    where the user actually types them in. For OAuth servers, we offer
    to run `brainchild auth <name>` inline so the browser popup happens
    while the user is already in setup context; declining leaves the
    user with the command they need to run later. For claude-code prereq
    gaps there's no in-wizard fix — just surface the hint + URL.
    """
    report = report_mcp_readiness(servers, enabled_only=True)
    if not report:
        console.print()
        console.print("  [dim]No MCPs enabled — skipping readiness check.[/dim]")
        console.input("\n  [dim]Press Enter to continue.[/dim] ")
        return

    console.print()
    console.print("  [bold]Readiness:[/bold]")
    _print_readiness_rows(report)

    # claude-code specifically needs a git-initialized repo at invocation
    # time (the MCP takes repo_path — not a wizard-time concern, a per-call
    # one). Remind users who enabled it so they aren't surprised later.
    # Surfacing here because there's no clean mid-meeting place to say it.
    if report.get("claude-code"):
        console.print()
        console.print(
            "  [dim]ℹ claude-code delegations need a git-initialized repo. "
            "If you point it at a folder without `.git`, the delegation will "
            "tell you to run `git init` — no crash.[/dim]"
        )

    # Offer inline auth for each oauth_needed server. Re-check after each
    # attempt so subsequent renders reflect the newly-seeded token.
    while True:
        pending = [n for n, rec in report.items() if rec["status"] == "oauth_needed"]
        if not pending:
            break
        name = pending[0]
        console.print()
        answer = Prompt.ask(
            f"  Authorize [bold]{name}[/bold] now? "
            f"[dim](browser popup; runs `brainchild auth {name}`)[/dim]",
            choices=["y", "n"],
            default="y",
        )
        if answer.lower() != "y":
            break
        console.print()
        # run_auth inherits stdout/stderr and blocks until the cache file
        # lands (or user aborts). It handles Ctrl+C cleanly; anything
        # non-zero means the user deferred, and we keep the current ⚠.
        rc = run_auth(name)
        console.print()
        if rc == 0:
            console.print(f"  [green]✓ {name} authorized.[/green]")
        else:
            console.print(f"  [yellow]⚠ {name} not authorized (exit {rc}) — "
                          f"run `brainchild auth {name}` later.[/yellow]")
        # Re-render so the user sees the updated state before the next
        # oauth_needed prompt (or fall-through to the acknowledgment pause).
        report = report_mcp_readiness(servers, enabled_only=True)
        console.print()
        console.print("  [bold]Readiness:[/bold]")
        _print_readiness_rows(report)

    console.input("\n  [dim]Press Enter to continue.[/dim] ")


def _print_readiness_rows(report: dict) -> None:
    """Render one ✓/⚠/✗ line per server with fix hint + URL.

    Status glyph colors (green / yellow / red) come from STATUS_GLYPH's
    key so callers in the wizard and runtime pre-flight render the same
    glyphs — just rich-tagged here. URLs print bare so the terminal can
    hyperlink them if the emulator supports it.
    """
    color = {
        "ok": "green",
        "oauth_needed": "yellow",
        "missing_env": "red",
        "prereq_missing": "red",
    }
    for name, rec in report.items():
        glyph = STATUS_GLYPH[rec["status"]]
        tag = color[rec["status"]]
        suffix = ""
        if rec["status"] != "ok":
            suffix = f" [dim]— {rec['fix']}[/dim]"
            if rec.get("fix_url"):
                suffix += f" [dim]({rec['fix_url']})[/dim]"
        console.print(f"    [{tag}]{glyph}[/{tag}] {name}{suffix}")


def _mcp_choice(name: str, *, locked_by: list[str] | None = None) -> Choice:
    """Render one MCP row. Officials get an `(official)` tag.

    When `locked_by` is a non-empty list of skill names, the row renders as
    locked-on with a caption naming the skill(s) that require this server.
    """
    tag = " (official)" if name in _OFFICIAL_MCPS else ""
    locked_by = locked_by or []
    return Choice(
        label=f"{name}{tag}",
        locked=bool(locked_by),
        locked_note=(
            f"required by: {', '.join(locked_by)}" if locked_by else ""
        ),
    )


def _required_mcps_from_skills(state: WizardState) -> dict[str, list[str]]:
    """Return {mcp_server_name: [skill_name, ...]} for every enabled skill
    that declares mcp-required in its frontmatter.

    Resolves state.enabled_skill_names against the shared library
    (~/.brainchild/skills/) + state.bot_cfg["skills"]["external_paths"].
    Uses the same load_skills path the runtime uses, so the wizard sees
    what the runtime will see. Unknown names are silently dropped (the
    loader already warns).
    """
    from brainchild.pipeline.skills import load_skills

    external = state.bot_cfg.get("skills", {}).get("external_paths") or []
    skills = load_skills(state.enabled_skill_names, external_paths=external)

    by_server: dict[str, list[str]] = {}
    for sk in skills:
        for server in sk.mcp_required:
            by_server.setdefault(server, []).append(sk.name)

    # Dedup skill names per server while preserving insertion order.
    return {s: list(dict.fromkeys(names)) for s, names in by_server.items()}


def _mcp_sort_key(name: str) -> tuple[int, str]:
    """Officials bucket first, claude-code last, everything else in between."""
    if name == "claude-code":
        return (2, name)
    if name in _OFFICIAL_MCPS:
        return (0, name)
    return (1, name)


# ── Step 3 — Skills ───────────────────────────────────────────────────────


def _step3_skills(state: WizardState, _unused: Path | None = None) -> None:
    """Mutates state.enabled_skill_names + state.bot_cfg["skills"]["external_paths"].

    Scans:
      - shared library (~/.brainchild/skills/)
      - state.bot_cfg["skills"]["external_paths"] (opt-in; tilde/absolute only)

    Dedups by skill name (list-order last-wins). Shows one picker with all
    discovered skills; source sublabel tells the user where each one came
    from. Default-checked = currently-enabled in bot_cfg (so edit-in-place
    preserves state, and new bots get the preset's defaults).

    Then offers an "Add external path" sub-prompt that appends to
    skills.external_paths — tilde-prefixed or absolute paths only, with
    the hint shown inline.
    """
    console.print("[bold]2. Skills[/bold]\n")

    state.bot_cfg.setdefault("skills", {})
    state.bot_cfg["skills"].setdefault("external_paths", [])
    state.bot_cfg["skills"].setdefault("progressive_disclosure", True)

    # Loop: show discovered skills + picker, optionally add more external
    # paths, re-scan after each addition so the picker reflects new sources.
    while True:
        candidates = _discover_skill_candidates(state)
        if not candidates:
            console.print(
                "  [dim]No skills found in the shared library or external_paths. "
                "Add an external path below to scan more locations.[/dim]\n"
            )
        else:
            _render_skill_picker(state, candidates)

        # Offer to add another external path. Loop until the user skips.
        if not _prompt_add_external_path(state):
            break


def _discover_skill_candidates(state: WizardState) -> list[tuple[str, str, str]]:
    """Scan shared library + configured external_paths; return [(name, description, source_label)].

    Last-wins dedup by name, with list order: library first, then each
    external path. source_label is a short tag shown in the picker row
    ("shared library" or "from ~/.claude/skills").
    """
    from brainchild.pipeline.skills import _resolve_external_path, _scan_skills_dir

    by_name: dict[str, tuple[str, str]] = {}  # name → (description, source_label)
    shared = Path.home() / ".brainchild" / "skills"
    if shared.is_dir():
        for sk in _scan_skills_dir(shared):
            by_name[sk.name] = (sk.description, "shared library")

    for raw in (state.bot_cfg.get("skills", {}).get("external_paths") or []):
        p = _resolve_external_path(raw)
        if p is None:
            continue
        for sk in _scan_skills_dir(p):
            by_name[sk.name] = (sk.description, f"from {raw}")

    return sorted(
        [(name, desc, src) for name, (desc, src) in by_name.items()],
        key=lambda t: t[0],
    )


def _render_skill_picker(
    state: WizardState,
    candidates: list[tuple[str, str, str]],
) -> None:
    """Present the unified skills picker and update state.enabled_skill_names."""
    # Preseed from state.enabled_skill_names (if populated) else from the
    # bot_cfg's skills.enabled list (edit-in-place) else from defaults (new
    # bot from preset → preset's bundled enabled list).
    current_enabled = set(state.enabled_skill_names) if state.enabled_skill_names else set(
        state.bot_cfg.get("skills", {}).get("enabled") or []
    )

    names = [c[0] for c in candidates]
    choices = [
        Choice(label=name, sublabel=f"{desc}  [{src}]")
        for name, desc, src in candidates
    ]
    initial = [n in current_enabled for n in names]

    def right_pane(_cursor, checked):
        enabled_now = [names[i] for i, on in enumerate(checked or []) if on]
        return state.card(skills=enabled_now)

    final = select_many(
        "",
        choices,
        initial_checked=initial,
        right_pane=right_pane,
        console=console,
    )
    state.enabled_skill_names = [names[i] for i, on in enumerate(final) if on]


def _prompt_add_external_path(state: WizardState) -> bool:
    """Prompt once for an additional external path. Returns True iff one
    was added (caller re-scans + re-renders). Returns False when the user
    leaves the input blank.

    Hard rule: paths MUST start with `~` or `/`. Relative paths are
    CWD-dependent at runtime, so we reject them here with a clear error.
    """
    console.print()
    console.print("  [dim]Add an external skills folder (tilde-prefixed or absolute, "
                  "e.g. `~/team-skills` or `/opt/skills`).[/dim]")
    raw = _prompt_with_hint("Leave empty to finish").strip()
    if not raw:
        return False
    if not (raw.startswith("~") or raw.startswith("/")):
        console.print(
            f"    [red]✗[/red] {raw!r} must start with `~` or `/`. "
            f"Relative paths are CWD-dependent and will WARN at runtime — use "
            f"a tilde-prefixed or absolute path."
        )
        return True  # keep looping so user can fix
    resolved = Path(os.path.expanduser(raw)).resolve()
    if not resolved.exists() or not resolved.is_dir():
        console.print(f"    [red]✗[/red] not a directory: {resolved}")
        return True
    paths = state.bot_cfg["skills"]["external_paths"]
    if raw in paths:
        console.print(f"    [dim]{raw} already added — skipping.[/dim]")
        return True
    paths.append(raw)
    console.print(f"    [green]✓[/green] added {raw}")
    return True


# ── Step 4 — System Prompt (personality + ground rules) ───────────────────


def _step4_system_prompt(state: WizardState) -> None:
    """Author the agent's system prompt — one input covers voice and rules.

    Stored on `personality`; `ground_rules` is cleared. config.py joins the
    two blocks with a blank line when either is non-empty, so leaving rules
    empty is fine — the user's one input flows straight through.
    """
    console.print("[bold]4. System Prompt[/bold]")
    console.print("  [dim]Give your agent personality and some ground rules.[/dim]\n")
    new_text = _prompt_with_hint("Leave empty to skip").strip()
    state.bot_cfg["personality"] = new_text
    state.bot_cfg["ground_rules"] = ""
    if new_text:
        console.print(f"  ✓ system prompt saved ({len(new_text)} chars)")
    else:
        console.print(f"  [dim]system prompt left blank[/dim]")

    # Claude preset: offer to append the user's ~/.claude/CLAUDE.md to
    # ground_rules. Opt-in (default N) because these files can be long
    # and project-specific content sometimes leaks in. Appending keeps
    # whatever the user just typed for personality intact.
    claude_md = getattr(state, "_claude_md_content", None)
    if state.based_on == "claude" and claude_md:
        console.print()
        answer = Prompt.ask(
            f"  Append your [bold]~/.claude/CLAUDE.md[/bold] ({len(claude_md)} chars) "
            f"to ground rules?",
            choices=["y", "n"],
            default="n",
        )
        if answer.lower() == "y":
            state.bot_cfg["ground_rules"] = claude_md.strip()
            console.print("  [green]✓[/green] ~/.claude/CLAUDE.md appended to ground rules.")


def _prompt_with_hint(hint: str) -> str:
    """Single-line input. Hint is dim-printed one line above — a workable
    stand-in for the in-field placeholder we'd use with prompt_toolkit.
    """
    console.print(f"  [dim]{hint}[/dim]")
    return Prompt.ask("  ›", default="", show_default=False)


# ── Step 5 — API keys ─────────────────────────────────────────────────────


def _step6_api_keys(needed: set[str]) -> None:
    console.print("\n[bold]5. API keys[/bold]")
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
    _AGENTS_DIR.mkdir(parents=True, exist_ok=True)

    tmp_parent = tempfile.mkdtemp(prefix=f".{state.name}.tmp-", dir=_AGENTS_DIR)
    tmp = Path(tmp_parent)

    target = _AGENTS_DIR / state.name
    backup: Path | None = None

    try:
        if state.mode == "edit" and target.exists():
            shutil.copytree(target, tmp, dirs_exist_ok=True)
            # Legacy per-agent skills dir (pre-15.11). It's no longer used —
            # skills live in the shared library ~/.brainchild/skills/. Clean
            # up so the bundle doesn't ship orphaned copies.
            legacy_skills = tmp / "skills"
            if legacy_skills.exists():
                shutil.rmtree(legacy_skills)

        # New skills block: `enabled: [names]` is canonical; `external_paths`
        # survives from the input config (in-place edits during step 3);
        # legacy `paths` key is dropped unconditionally.
        state.bot_cfg.setdefault("skills", {})
        state.bot_cfg["skills"]["enabled"] = list(state.enabled_skill_names)
        state.bot_cfg["skills"].setdefault("external_paths", [])
        state.bot_cfg["skills"].setdefault("progressive_disclosure", True)
        state.bot_cfg["skills"].pop("paths", None)

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

    return target


def _write_readme(path: Path, name: str, bot_cfg: dict) -> None:
    tagline = (bot_cfg.get("agent") or {}).get("tagline", "") or ""
    display = (bot_cfg.get("agent") or {}).get("name", name)
    mcps = [k for k, v in (bot_cfg.get("mcp_servers") or {}).items() if v.get("enabled")]
    mcp_line = ", ".join(mcps) if mcps else "(none enabled)"

    body = (
        f"# {display}\n\n"
        f"{tagline}\n\n"
        f"Run: `brainchild run {name}` or `brainchild run {name} <meet-url>`.\n\n"
        f"MCPs: {mcp_line}\n\n"
        "## Note\n\n"
        "Skills and MCPs are independent in this bundle — enabling a skill\n"
        "that references an MCP tool doesn't auto-enable the MCP, and vice\n"
        "versa. If a skill asks for a tool that isn't wired, the model will\n"
        "either ask for it or degrade gracefully. Re-run `brainchild setup`\n"
        "and pick this agent as a preset to adjust either list.\n"
    )
    path.write_text(body, encoding="utf-8")


# ── Reveal ────────────────────────────────────────────────────────────────


def _reveal(state: WizardState) -> None:
    """Final card render — placeholder portrait swaps for the real one."""
    state.portrait = face.load_or_render(
        state.name, _AGENTS_DIR / state.name / "portrait.txt",
    )
    config_path = f"~/.brainchild/agents/{state.name}/config.yaml"
    console.print()
    console.print("[bold]✨ All set! 🎁[/bold]")
    console.print()
    console.print(f"Your agent config lives in [bold]{config_path}[/bold].")
    console.print()
    console.print(f"Take [bold]{state.name}[/bold] for a spin: [bold]brainchild run {state.name}[/bold]")
    console.print()
    console.print(state.card(title=f"Meet {state.name}"))


# ── Entry point ───────────────────────────────────────────────────────────


def run(argv: list[str]) -> int:
    """CLI entry. argv is ignored today; kept for future flags like --dry-run."""
    console.print()
    console.print("[bold]Brainchild setup wizard[/bold]")
    console.print("[dim]Six steps. Ctrl+C / q at any picker cancels without writing.[/dim]\n")
    try:
        state = _step1_fighter_select()

        # Skills first so step 3 (MCPs) can lock MCPs required by chosen skills.
        console.clear()
        _step3_skills(state)

        console.clear()
        _step2_mcps(state)

        console.clear()
        _step4_system_prompt(state)

        console.clear()
        envs = _collect_env_refs(state)
        _step6_api_keys(envs)

        console.print()
        console.input("  [bold]Press Enter to reveal your agent ✨🎁[/bold] ")

        console.clear()
        _step7_write(state)
        _reveal(state)
    except (KeyboardInterrupt, PickerCancelled, WizardCancel):
        console.print("\nCancelled.")
        return 1
    except Exception as e:
        console.print(f"\n✗ setup failed: {e}")
        raise

    console.print()
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
