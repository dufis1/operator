"""The 'Your build' card — persistent right-pane preview during the
``operator setup`` wizard's picker steps and the final reveal artifact.

Frame is rendered with a per-character rainbow: every border glyph
(``╭ ─ ╮ │ ╰ ╯``) cycles through red/green/yellow/blue/magenta. Body
text is plain white — the card is the only colored element in the
wizard. See ``_rainbow_wrap`` for the cycling logic.
"""
from __future__ import annotations

from itertools import cycle

from rich.cells import cell_len
from rich.console import RenderableType
from rich.text import Text


PLACEHOLDER_PORTRAIT = (
    "▄▄▄▄▄▄\n"
    "█ ?? █\n"
    "█ ?? █\n"
    "▀▀▀▀▀▀"
)

_COLORS = [
    "\033[0;31;40m",  # red
    "\033[0;32;40m",  # green
    "\033[0;33;40m",  # yellow
    "\033[0;34;40m",  # blue
    "\033[0;35;40m",  # magenta
]
_RESET = "\033[0m"

_WIDTH = 40
_INNER = _WIDTH - 2  # 38


def _compose_body(
    name: str,
    tagline: str,
    portrait: str,
    power_ups: list[str],
    skills: list[str],
) -> list[str]:
    """Plain-text body rows — no ANSI, no Rich markup."""
    portrait_lines = portrait.split("\n")
    portrait_w = max((cell_len(p) for p in portrait_lines), default=0)
    meta_lines = [name or "(unnamed)", tagline or "(no tagline yet)"]

    rows: list[str] = []
    for i in range(max(len(portrait_lines), len(meta_lines))):
        p = portrait_lines[i] if i < len(portrait_lines) else ""
        m = meta_lines[i] if i < len(meta_lines) else ""
        left = "  " + p + " " * (portrait_w - cell_len(p))
        rows.append(left + "   " + m)

    rows.append("")

    if power_ups:
        rows.append("  power-ups:  " + "   ".join(f"⚡ {x}" for x in power_ups))
    else:
        rows.append("  power-ups:  —")

    if skills:
        rows.append("  skills:     " + "   ".join(f"★ {x}" for x in skills))
    else:
        rows.append("  skills:     —")

    return rows


def _rainbow_wrap(body_rows: list[str], title: str) -> str:
    """Wrap body rows in a per-character rainbow frame. Returns raw ANSI."""
    c = cycle(_COLORS)

    title_str = f" {title} "
    remaining = _INNER - cell_len(title_str)
    left_fill = max(0, remaining // 2)
    right_fill = max(0, remaining - left_fill)
    top_chars = ["╭"] + ["─"] * left_fill + list(title_str) + ["─"] * right_fill + ["╮"]
    top = "".join(f"{next(c)}{ch}{_RESET}" for ch in top_chars)

    body_out: list[str] = []
    for line in body_rows:
        truncated = line
        while cell_len(truncated) > _INNER:
            truncated = truncated[:-1]
        pad = " " * max(0, _INNER - cell_len(truncated))
        left = f"{next(c)}│{_RESET}"
        right = f"{next(c)}│{_RESET}"
        body_out.append(f"{left}{truncated}{pad}{right}")

    bot_chars = ["╰"] + ["─"] * _INNER + ["╯"]
    bot = "".join(f"{next(c)}{ch}{_RESET}" for ch in bot_chars)

    return "\n".join([top, *body_out, bot])


def render(
    *,
    name: str,
    tagline: str,
    portrait: str,
    power_ups: list[str],
    skills: list[str],
    title: str = "Your build",
) -> RenderableType:
    """Build the card as a Rich renderable with a rainbow frame."""
    body = _compose_body(name, tagline, portrait, power_ups, skills)
    framed = _rainbow_wrap([""] + body + [""], title)
    return Text.from_ansi(framed)
