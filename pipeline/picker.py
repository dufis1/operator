"""Arrow-key picker primitives for the `operator setup` wizard.

Two flavors share one rendering core:

- :func:`select_one` — single-select. ↑/↓ navigate, enter pick. An optional
  preview pane on the right updates as the cursor moves (used by step 1's
  fighter gallery to show portraits).
- :func:`select_many` — multi-select. ↑/↓ navigate, space toggle, enter
  confirm. Used by step 2 (MCPs) and step 3 (skills).

Both use :class:`rich.live.Live` to redraw one region in place and
``readchar`` for cross-platform key reads. Tests inject keys via the
``key_source`` parameter so the picker can run without a TTY.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator

import readchar
from rich.console import Console, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# Right-pane renderer signature: receives (cursor, checked) and returns a
# Rich renderable. Used when the wizard wants the right pane to reflect
# global state (e.g., the build card in steps 2/3) instead of a per-choice
# preview.
RightPaneFn = Callable[[int, "list[bool] | None"], RenderableType]


@dataclass
class Choice:
    """One row in a picker.

    label    — main text shown on the left
    sublabel — dim text shown next to label (e.g. tagline)
    value    — opaque payload returned to the caller
    preview  — multi-line text rendered in the right pane (single-select only)
    """

    label: str
    sublabel: str = ""
    value: Any = None
    preview: str | None = None


class PickerCancelled(Exception):
    """User pressed q / esc / ctrl+c while the picker was open."""


# ── Render core ───────────────────────────────────────────────────────────


def _render_rows(
    title: str,
    choices: list[Choice],
    cursor: int,
    *,
    checked: list[bool] | None,
    hint: str,
) -> Text:
    """Build the choice list as a single Rich Text block."""
    body = Text()
    body.append(f"{title}\n\n", style="bold")
    for i, ch in enumerate(choices):
        is_cursor = i == cursor
        cursor_glyph = "▶ " if is_cursor else "  "
        check_glyph = ""
        if checked is not None:
            check_glyph = "[✓] " if checked[i] else "[ ] "
        line = Text()
        line.append(cursor_glyph, style="bold cyan" if is_cursor else "")
        line.append(check_glyph)
        line.append(ch.label, style="bold" if is_cursor else "")
        if ch.sublabel:
            line.append(f"   {ch.sublabel}", style="dim")
        body.append(line)
        body.append("\n")
    if hint:
        body.append(f"\n{hint}", style="dim")
    return body


def _layout(
    title: str,
    choices: list[Choice],
    cursor: int,
    *,
    checked: list[bool] | None,
    hint: str,
    right_pane: RightPaneFn | None,
) -> RenderableType:
    rows = _render_rows(title, choices, cursor, checked=checked, hint=hint)
    right: RenderableType | None = None
    if right_pane is not None:
        right = right_pane(cursor, checked)
    elif choices[cursor].preview is not None:
        right = Panel(
            Text(choices[cursor].preview), border_style="dim", padding=(0, 2),
        )
    if right is None:
        return rows
    table = Table.grid(padding=(0, 4))
    table.add_column()
    table.add_column()
    table.add_row(rows, right)
    return table


# ── Key handling ──────────────────────────────────────────────────────────


def _default_keys() -> Iterator[str]:
    """Yield keys forever from readchar — used at runtime."""
    while True:
        yield readchar.readkey()


def _is_enter(key: str) -> bool:
    return key in ("\n", "\r", readchar.key.ENTER)


def _is_cancel(key: str) -> bool:
    return key in ("q", "Q", readchar.key.ESC, readchar.key.CTRL_C)


# ── Public API ────────────────────────────────────────────────────────────


def select_one(
    title: str,
    choices: list[Choice],
    *,
    right_pane: RightPaneFn | None = None,
    initial: int = 0,
    console: Console | None = None,
    key_source: Iterable[str] | None = None,
) -> Choice:
    """Single-select picker. Returns the picked Choice.

    Right pane behavior: ``right_pane`` callable wins; else falls back to
    each choice's ``preview`` field; else no right pane is drawn.

    Raises PickerCancelled on q/esc/ctrl+c.
    """
    if not choices:
        raise ValueError("select_one needs at least one choice")
    cursor = max(0, min(initial, len(choices) - 1))
    hint = "↑/↓ navigate · enter select · q to cancel"
    console = console or Console()
    keys: Iterator[str] = iter(key_source) if key_source is not None else _default_keys()

    def render() -> RenderableType:
        return _layout(
            title, choices, cursor,
            checked=None, hint=hint, right_pane=right_pane,
        )

    with Live(render(), console=console, refresh_per_second=30, transient=False) as live:
        for key in keys:
            if key == readchar.key.UP:
                cursor = (cursor - 1) % len(choices)
            elif key == readchar.key.DOWN:
                cursor = (cursor + 1) % len(choices)
            elif _is_enter(key):
                live.update(render())
                return choices[cursor]
            elif _is_cancel(key):
                raise PickerCancelled()
            live.update(render())
    raise PickerCancelled()  # key source exhausted without enter


def select_many(
    title: str,
    choices: list[Choice],
    *,
    initial_checked: list[bool] | None = None,
    right_pane: RightPaneFn | None = None,
    console: Console | None = None,
    key_source: Iterable[str] | None = None,
) -> list[bool]:
    """Multi-select picker. Returns aligned list[bool].

    Right pane (if provided) re-renders on every keypress so the wizard's
    build card can reflect live toggle state.

    Raises PickerCancelled on q/esc/ctrl+c. Empty choices returns [].
    """
    if not choices:
        return []
    if initial_checked is None:
        checked = [False] * len(choices)
    else:
        checked = list(initial_checked)
    cursor = 0
    hint = "↑/↓ navigate · space toggle · enter confirm · q to cancel"
    console = console or Console()
    keys: Iterator[str] = iter(key_source) if key_source is not None else _default_keys()

    def render() -> RenderableType:
        return _layout(
            title, choices, cursor,
            checked=checked, hint=hint, right_pane=right_pane,
        )

    with Live(render(), console=console, refresh_per_second=30, transient=False) as live:
        for key in keys:
            if key == readchar.key.UP:
                cursor = (cursor - 1) % len(choices)
            elif key == readchar.key.DOWN:
                cursor = (cursor + 1) % len(choices)
            elif key in (" ", readchar.key.SPACE):
                checked[cursor] = not checked[cursor]
            elif _is_enter(key):
                live.update(render())
                return checked
            elif _is_cancel(key):
                raise PickerCancelled()
            live.update(render())
    raise PickerCancelled()
