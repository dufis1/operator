"""The 'Your build' card — persistent right-pane preview during the
``operator setup`` wizard's picker steps and the final reveal artifact.

All wrapping happens inside ``_compose_body`` so every emitted row is
already ≤ ``_INNER`` cells with the ``"  "`` left indent preserved
(hanging indent on continuations). The frame then just decorates —
Panel for steps 2 & 3 (``rainbow=False``), per-glyph rainbow ANSI for
the reveal (``rainbow=True``).

Tagline is wrapped into the right-hand meta column *before* being
zipped with the portrait, so a long tagline extends meta_lines
downward without breaking the face grid.
"""
from __future__ import annotations

from itertools import cycle

from rich.cells import cell_len
from rich.console import RenderableType
from rich.panel import Panel
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


def _wrap_cells(text: str, width: int) -> list[str]:
    """Wrap ``text`` on spaces so each line fits ``width`` cells.

    A single token wider than ``width`` is hard-split on code-point
    boundaries — acceptable for the build card since the only wide
    glyphs in play (⚡ ★) always sit next to short ASCII labels.
    """
    if width <= 0:
        return [text]
    if cell_len(text) <= width:
        return [text]
    out: list[str] = []
    cur = ""
    for word in text.split(" "):
        candidate = f"{cur} {word}" if cur else word
        if cell_len(candidate) <= width:
            cur = candidate
            continue
        if cur:
            out.append(cur)
            cur = ""
        while cell_len(word) > width:
            out.append(word[:width])
            word = word[width:]
        cur = word
    if cur:
        out.append(cur)
    return out


def _hang_wrap(prefix: str, body: str, inner: str = "", *, width: int) -> list[str]:
    """Wrap ``body`` under a label ``prefix``. First line renders
    ``prefix + body-segment``; continuation lines are padded so body
    text aligns under itself (hanging indent at ``len(prefix)``).

    ``inner`` is an optional left gutter applied to every emitted row
    (the card's ``"  "`` indent).
    """
    avail = width - cell_len(inner) - cell_len(prefix)
    segs = _wrap_cells(body, avail)
    if not segs:
        return [inner + prefix]
    hang = " " * cell_len(prefix)
    rows = [inner + prefix + segs[0]]
    for seg in segs[1:]:
        rows.append(inner + hang + seg)
    return rows


def _compose_body(
    name: str,
    tagline: str,
    portrait: str,
    power_ups: list[str],
    skills: list[str],
) -> list[str]:
    """Plain-text body rows — no ANSI, no Rich markup. Every row fits
    within ``_INNER`` cells and carries the ``"  "`` left indent."""
    indent = "  "
    portrait_lines = portrait.split("\n")
    portrait_w = max((cell_len(p) for p in portrait_lines), default=0)

    # Wrap tagline into the meta column BEFORE zipping with the portrait,
    # so a long tagline just adds rows under the name — it can't wrap
    # across the face.
    meta_col_w = _INNER - cell_len(indent) - portrait_w - 3
    meta_lines: list[str] = [name or "(unnamed)"]
    meta_lines += _wrap_cells(tagline or "(no tagline yet)", meta_col_w)

    rows: list[str] = []
    for i in range(max(len(portrait_lines), len(meta_lines))):
        p = portrait_lines[i] if i < len(portrait_lines) else ""
        m = meta_lines[i] if i < len(meta_lines) else ""
        left = indent + p + " " * (portrait_w - cell_len(p))
        row = left + "   " + m
        rows.append(row + " " * max(0, _INNER - cell_len(row)))

    rows.append(" " * _INNER)

    power_body = "   ".join(f"⚡ {x}" for x in power_ups) if power_ups else "—"
    rows.extend(_hang_wrap("power-ups:  ", power_body, width=_INNER, inner=indent))

    skill_body = "   ".join(f"★ {x}" for x in skills) if skills else "—"
    rows.extend(_hang_wrap("skills:     ", skill_body, width=_INNER, inner=indent))

    # Normalize every row to exactly _INNER cells so the Panel renders
    # with clean right-edge padding and no Rich reflow.
    return [r + " " * max(0, _INNER - cell_len(r)) for r in rows]


def _rainbow_wrap(body_rows: list[str], title: str) -> str:
    """Wrap body rows in a per-character rainbow frame. Returns raw ANSI.

    Rows are assumed pre-padded to ``_INNER`` cells by ``_compose_body``.
    """
    c = cycle(_COLORS)

    title_str = f" {title} "
    remaining = _INNER - cell_len(title_str)
    left_fill = max(0, remaining // 2)
    right_fill = max(0, remaining - left_fill)
    top_chars = ["╭"] + ["─"] * left_fill + list(title_str) + ["─"] * right_fill + ["╮"]
    top = "".join(f"{next(c)}{ch}{_RESET}" for ch in top_chars)

    body_out: list[str] = []
    for line in body_rows:
        pad = " " * max(0, _INNER - cell_len(line))
        left = f"{next(c)}│{_RESET}"
        right = f"{next(c)}│{_RESET}"
        body_out.append(f"{left}{line}{pad}{right}")

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
    rainbow: bool = False,
) -> RenderableType:
    """Build the card as a Rich renderable.

    ``rainbow=False`` (default) draws a plain white ``Panel`` that
    wraps long lines. ``rainbow=True`` draws the per-character rainbow
    frame; long lines are truncated to the inner width.
    """
    body = _compose_body(name, tagline, portrait, power_ups, skills)
    blank = " " * _INNER
    framed_body = [blank] + body + [blank]
    if rainbow:
        return Text.from_ansi(_rainbow_wrap(framed_body, title))
    # Pre-padded rows fit _INNER exactly — turn off Rich's reflow so it
    # doesn't strip leading indent on any row.
    inner = Text("\n".join(framed_body), no_wrap=True, overflow="crop")
    return Panel(
        inner,
        title=title,
        border_style="white",
        width=_WIDTH,
        padding=(0, 0),
    )
