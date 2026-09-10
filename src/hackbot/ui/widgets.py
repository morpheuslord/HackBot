"""Reusable terminal widgets built on Rich primitives.

- ScrollState / ScrollView : scroll any renderable inside a fixed-height area
- TextInput                : single-line editor with cursor (optionally masked)
- Popup                    : message / confirm / input / view / list / palette dialogs (subclassable)
- Composite                : draws a popup *over* a base screen (segment-level overlay)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from rich import box
from rich.console import Console, ConsoleOptions, Group, RenderableType, RenderResult
from rich.panel import Panel
from rich.segment import Segment
from rich.table import Table
from rich.text import Text

from .keys import Key


# ------------------------------------------------------------------ scrolling
@dataclass
class ScrollState:
    offset: int = 0
    max_offset: int = 0
    page: int = 10
    stick_bottom: bool = False  # follow new content (chat transcript)

    def up(self, n: int = 1) -> None:
        self.stick_bottom = False
        self.offset = max(0, self.offset - n)

    def down(self, n: int = 1) -> None:
        self.offset = min(self.max_offset, self.offset + n)
        if self.offset >= self.max_offset:
            self.stick_bottom = True

    def home(self) -> None:
        self.stick_bottom = False
        self.offset = 0

    def end(self) -> None:
        self.stick_bottom = True
        self.offset = self.max_offset

    def handle(self, key: Key) -> bool:
        actions = {
            "up": lambda: self.up(1),
            "down": lambda: self.down(1),
            "pageup": lambda: self.up(max(1, self.page - 1)),
            "pagedown": lambda: self.down(max(1, self.page - 1)),
            "home": self.home,
            "end": self.end,
        }
        if key.name in actions:
            actions[key.name]()
            return True
        return False


class ScrollView:
    """Renders `renderable` at full height, then shows the window at state.offset."""

    def __init__(self, renderable: RenderableType, state: ScrollState):
        self.renderable = renderable
        self.state = state

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        height = options.height or console.height
        width = options.max_width
        lines = console.render_lines(self.renderable, options.update(width=width, height=None), pad=True)
        st = self.state
        st.page = height
        st.max_offset = max(0, len(lines) - height)
        if st.stick_bottom:
            st.offset = st.max_offset
        st.offset = min(st.offset, st.max_offset)
        view = lines[st.offset : st.offset + height]
        blank = [Segment(" " * width)]
        view += [blank] * (height - len(view))
        newline = Segment.line()
        for line in view:
            yield from line
            yield newline


class FixedHeight:
    """Render a renderable into exactly `height` rows (crop or pad)."""

    def __init__(self, renderable: RenderableType, height: int):
        self.renderable, self.height = renderable, max(1, height)

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        lines = console.render_lines(self.renderable, options.update(height=self.height), pad=True)
        newline = Segment.line()
        for line in lines:
            yield from line
            yield newline


# ------------------------------------------------------------------ text input
class TextInput:
    def __init__(self, text: str = "", placeholder: str = "", mask: bool = False):
        self.text = text
        self.cursor = len(text)
        self.placeholder = placeholder
        self.mask = mask

    def clear(self) -> None:
        self.text, self.cursor = "", 0

    def handle(self, key: Key) -> bool:
        if key.name == "char":
            self.text = self.text[: self.cursor] + key.char + self.text[self.cursor :]
            self.cursor += 1
        elif key.name == "backspace":
            if self.cursor:
                self.text = self.text[: self.cursor - 1] + self.text[self.cursor :]
                self.cursor -= 1
        elif key.name == "delete":
            self.text = self.text[: self.cursor] + self.text[self.cursor + 1 :]
        elif key.name == "left":
            self.cursor = max(0, self.cursor - 1)
        elif key.name == "right":
            self.cursor = min(len(self.text), self.cursor + 1)
        elif key.name == "home":
            self.cursor = 0
        elif key.name == "end":
            self.cursor = len(self.text)
        elif key.name == "ctrl+u":
            self.clear()
        else:
            return False
        return True

    def render(self, width: int, focused: bool = True) -> Text:
        width = max(4, width)
        if not self.text and self.placeholder:
            t = Text(self.placeholder[: width - 1], style="dim italic")
            if focused:
                t.stylize("reverse", 0, 1)
            return t
        shown = ("*" * len(self.text)) if self.mask else self.text
        start = max(0, self.cursor - width + 2)
        visible = shown[start : start + width - 1] + " "
        t = Text(visible)
        if focused:
            c = self.cursor - start
            t.stylize("reverse", c, c + 1)
        return t


# ------------------------------------------------------------------ popups
class Popup:
    """Modal dialog. kind: 'message' | 'confirm' | 'input' | 'view' | 'list' | 'palette'.

    * list    - options: list[(label, value)]; Enter -> on_submit(value)
    * palette - options: list[(label, hint, value)] filtered by typed text; Enter -> on_submit(value)
                (or on_submit(typed text) when nothing matches and allow_free_text)
    Subclass and override `handle` / `body_renderable` for custom windows.
    """

    def __init__(
        self,
        title: str,
        body: RenderableType | str = "",
        kind: str = "message",
        on_submit: Callable[[Any], None] | None = None,
        on_close: Callable[[], None] | None = None,
        width: int = 64,
        height: int | None = None,
        placeholder: str = "",
        initial: str = "",
        style: str = "cyan",
        options: list[tuple] | None = None,
        mask: bool = False,
        hint: str | None = None,
        allow_free_text: bool = False,
    ):
        self.title, self.kind = title, kind
        self.on_submit, self.on_close = on_submit, on_close
        self.body = Text(body) if isinstance(body, str) else body
        self.width, self.height, self.style = width, height, style
        self.input = TextInput(initial, placeholder, mask=mask) if kind in ("input", "palette") else None
        self.scroll = ScrollState()
        self.options: list[tuple] = list(options or [])
        self.selected = 0
        self.custom_hint = hint
        self.allow_free_text = allow_free_text
        self._closed = False

    # --------------------------------------------------------------- data
    def visible_options(self) -> list[tuple]:
        if self.kind != "palette" or not self.input or not self.input.text.strip():
            return self.options
        needle = self.input.text.strip().lower()
        return [o for o in self.options if needle in (o[0] + " " + str(o[1] if len(o) > 2 else "")).lower()]

    # --------------------------------------------------------------- keys
    def handle(self, key: Key) -> bool:
        """Returns True when the popup should close."""
        if key.name == "escape":
            return True
        if key.name == "ctrl+c":
            return False
        if self.kind == "input" and self.input:
            if key.name == "enter":
                if self.on_submit:
                    self.on_submit(self.input.text.strip())
                return True
            self.input.handle(key)
            return False
        if self.kind == "confirm":
            if (key.name == "char" and key.char.lower() == "y") or key.name == "enter":
                if self.on_submit:
                    self.on_submit("yes")
                return True
            return key.name == "char" and key.char.lower() == "n"
        if self.kind in ("list", "palette"):
            opts = self.visible_options()
            if key.name == "up":
                self.selected = max(0, self.selected - 1)
            elif key.name == "down":
                self.selected = min(max(0, len(opts) - 1), self.selected + 1)
            elif key.name == "pageup":
                self.selected = max(0, self.selected - 8)
            elif key.name == "pagedown":
                self.selected = min(max(0, len(opts) - 1), self.selected + 8)
            elif key.name == "enter":
                if opts:
                    value = opts[min(self.selected, len(opts) - 1)][-1]
                    if self.on_submit:
                        self.on_submit(value)
                    return True
                if self.allow_free_text and self.input and self.input.text.strip() and self.on_submit:
                    self.on_submit(self.input.text.strip())
                    return True
                return False
            elif self.kind == "palette" and self.input and self.input.handle(key):
                self.selected = 0
            return False
        if self.kind == "view" and self.scroll.handle(key):
            return False
        return key.name in ("enter", "char")  # message/view: any key closes

    # --------------------------------------------------------------- layout
    def size(self, console: Console, max_w: int, max_h: int) -> tuple[int, int]:
        w = min(self.width, max_w - 2)
        if self.height:
            return w, min(self.height, max_h - 1)
        if self.kind == "list":
            return w, min(len(self.options) + 4, max_h - 2)
        if self.kind == "palette":
            return w, min(20, max_h - 2)
        lines = len(console.render_lines(self.body, console.options.update(width=w - 4, height=None)))
        extra = 4 if self.kind == "input" else 3  # borders + hint (+ input row)
        return w, min(lines + extra, max_h - 1)

    def hint(self) -> Text:
        hints = {
            "message": "Enter / Esc  close",
            "confirm": "y  yes    n / Esc  no",
            "input": "Enter  ok    Esc  cancel",
            "view": "Up/Down/PgUp/PgDn  scroll    Esc  close",
            "list": "Up/Down  select    Enter  choose    Esc  close",
            "palette": "type to filter    Up/Down  select    Enter  run    Esc  close",
        }
        return Text(self.custom_hint or hints[self.kind], style="dim", justify="right")

    def _rows(self, width: int, rows: int) -> Table:
        opts = self.visible_options()
        self.selected = min(self.selected, max(0, len(opts) - 1))
        top = max(0, min(self.selected - rows // 2, len(opts) - rows))
        table = Table.grid(expand=True, padding=(0, 1))
        table.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        if self.kind == "palette":
            table.add_column(no_wrap=True, overflow="ellipsis", style="dim", max_width=width // 3)
        for i, opt in enumerate(opts[top : top + rows], start=top):
            style = "reverse" if i == self.selected else ""
            cells = [Text(str(opt[0]), style=style)]
            if self.kind == "palette":
                cells.append(Text(str(opt[1]) if len(opt) > 2 else "", style=style))
            table.add_row(*cells)
        if not opts:
            table.add_row(Text("no matches", style="dim italic"), *(["" ] if self.kind == "palette" else []))
        return table

    def body_renderable(self, width: int, inner_h: int) -> RenderableType:
        if self.kind == "view":
            return FixedHeight(ScrollView(self.body, self.scroll), inner_h - 1)
        if self.kind == "list":
            parts: list[RenderableType] = []
            body_lines = 0
            if isinstance(self.body, Text) and self.body.plain or not isinstance(self.body, Text):
                parts.append(self.body)
                body_lines = 2
            parts.append(self._rows(width, max(1, inner_h - 1 - body_lines)))
            return FixedHeight(Group(*parts), inner_h - 1)
        if self.kind == "palette":
            prompt = Text.assemble(("> ", "bold"), self.input.render(width - 6) if self.input else Text())
            return FixedHeight(Group(prompt, Text(""), self._rows(width, max(1, inner_h - 3))), inner_h - 1)
        parts = [self.body]
        if self.input is not None:
            parts.append(Text.assemble(("> ", "bold"), self.input.render(width - 6)))
        return Group(*parts)

    def render(self, width: int, height: int) -> Panel:
        content = Group(self.body_renderable(width, height - 2), self.hint())
        return Panel(content, title=f"[bold]{self.title}[/]", border_style=self.style, box=box.DOUBLE, padding=(0, 1))


# ------------------------------------------------------------------ overlay
class Composite:
    """Render `base` full-screen, then paint `popup` centred on top of it."""

    def __init__(self, base: RenderableType, popup: Popup | None):
        self.base, self.popup = base, popup

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        w = options.max_width
        h = options.height or console.height
        lines = console.render_lines(self.base, options.update(width=w, height=h), pad=True)
        if self.popup is not None:
            pw, ph = self.popup.size(console, w, h)
            x0, y0 = (w - pw) // 2, (h - ph) // 2
            pop = console.render_lines(self.popup.render(pw, ph), options.update(width=pw, height=ph), pad=True)
            for i, pline in enumerate(pop):
                y = y0 + i
                if y >= len(lines):
                    break
                left, _, right = Segment.divide(lines[y], [x0, x0 + pw, w])
                lines[y] = left + pline + right
        newline = Segment.line()
        for line in lines:
            yield from line
            yield newline
