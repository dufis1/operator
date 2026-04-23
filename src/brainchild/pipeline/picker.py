"""Arrow-key picker primitives for the `brainchild setup` wizard.

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

    label       — main text shown on the left
    sublabel    — dim text shown next to label (e.g. tagline)
    value       — opaque payload returned to the caller
    preview     — rendered in the right pane (single-select only).
                  Plain string or any Rich RenderableType (Group/Align/etc.).
    locked      — multi-select only. When True the row renders dim with a
                  lock glyph, space is a no-op, and the row is forced checked
                  at init. Used for MCPs required by chosen skills.
    locked_note — dim text appended after the label on locked rows
                  (e.g. "required by: pr-review, release-notes").
    """

    label: str
    sublabel: str = ""
    value: Any = None
    preview: "str | RenderableType | None" = None
    locked: bool = False
    locked_note: str = ""


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
    if title:
        body.append(f"{title}\n\n", style="bold")
    sub_indent = "      " if checked is not None else "  "
    for i, ch in enumerate(choices):
        is_cursor = i == cursor
        is_locked = bool(ch.locked) and checked is not None
        cursor_glyph = "▶ " if is_cursor else "  "
        check_glyph = ""
        if checked is not None:
            # Locked rows always show ✓ — they're forced-on and can't be toggled.
            check_glyph = "[✓] " if (checked[i] or is_locked) else "[ ] "
        line = Text()
        line.append(cursor_glyph, style="bold" if is_cursor else "")
        line.append(check_glyph, style="dim" if is_locked else "")
        label_style = "bold" if is_cursor else ""
        if is_locked:
            # Dim the whole row so it reads as "present but you can't change it".
            label_style = "dim"
        line.append(ch.label, style=label_style)
        if is_locked and ch.locked_note:
            line.append(f"  ({ch.locked_note})", style="dim")
        body.append(line)
        body.append("\n")
        if ch.sublabel:
            sub = Text()
            sub.append(sub_indent)
            sub.append(ch.sublabel, style="dim")
            body.append(sub)
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
        preview = choices[cursor].preview
        inner: RenderableType = Text(preview) if isinstance(preview, str) else preview
        right = Panel(inner, border_style="dim", padding=(0, 2))
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
    # Locked choices are forced on regardless of initial_checked — the caller
    # has already decided the row is non-negotiable (e.g. an MCP required by a
    # chosen skill). Defensive: downstream code depends on locked ⇒ checked.
    for i, ch in enumerate(choices):
        if ch.locked:
            checked[i] = True
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
                # No-op on locked rows; the UX caption explains why.
                if not choices[cursor].locked:
                    checked[cursor] = not checked[cursor]
            elif _is_enter(key):
                live.update(render())
                return checked
            elif _is_cancel(key):
                raise PickerCancelled()
            live.update(render())
    raise PickerCancelled()
