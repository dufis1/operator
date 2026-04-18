"""The 'Your build' card — persistent right-pane preview during the
``operator setup`` wizard's picker steps and the final reveal artifact.

Layout:

    ╭─ Your build ──────────────────────────────╮
    │  ▄▄▄▄▄▄                                   │
    │  █ ⊙⊙ █     researcher                    │
    │  █ ‿‿ █     turns decisions into Linear   │
    │  ▀▀▀▀▀▀                                   │
    │                                           │
    │  power-ups:  ⚡ linear   ⚡ github         │
    │  skills:     ★ standup-summary             │
    ╰───────────────────────────────────────────╯

In custom mode the portrait is the placeholder ``?`` frame until the final
reveal in step 5; in edit-in-place mode it's the bot's existing portrait
from the start (no surprise to give).
"""
from __future__ import annotations

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


PLACEHOLDER_PORTRAIT = (
    "▄▄▄▄▄▄\n"
    "█ ?? █\n"
    "█ ?? █\n"
    "▀▀▀▀▀▀"
)


def render(
    *,
    name: str,
    tagline: str,
    portrait: str,
    power_ups: list[str],
    skills: list[str],
    title: str = "Your build",
) -> RenderableType:
    """Build the card as a Rich Panel renderable."""
    # ── Top half — portrait | name/tagline ────────────────────────────────
    portrait_text = Text(portrait, style="bold")
    meta = Text()
    meta.append(name or "(unnamed)", style="bold")
    meta.append("\n")
    meta.append(tagline or "(no tagline yet)", style="dim italic" if not tagline else "")

    top = Table.grid(padding=(0, 3))
    top.add_column()
    top.add_column()
    top.add_row(portrait_text, meta)

    # ── Bottom half — equipped rows ───────────────────────────────────────
    power_line = Text()
    power_line.append("power-ups:  ", style="dim")
    if power_ups:
        for i, mcp in enumerate(power_ups):
            if i:
                power_line.append("   ")
            power_line.append("⚡ ", style="yellow")
            power_line.append(mcp)
    else:
        power_line.append("—", style="dim")

    skills_line = Text()
    skills_line.append("skills:     ", style="dim")
    if skills:
        for i, sk in enumerate(skills):
            if i:
                skills_line.append("   ")
            skills_line.append("★ ", style="cyan")
            skills_line.append(sk)
    else:
        skills_line.append("—", style="dim")

    body = Group(top, Text(""), power_line, skills_line)
    return Panel(body, title=title, border_style="bright_magenta", padding=(1, 2), width=40)
