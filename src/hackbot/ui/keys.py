"""Cross-platform, non-blocking single-key reader (Rich draws; this listens).

Key names: "char" (with .char), "enter", "backspace", "tab", "escape", "delete",
"up", "down", "left", "right", "pageup", "pagedown", "home", "end", "f1".."f12",
"ctrl+a".."ctrl+z", "ctrl+up/down/left/right".
"""

from __future__ import annotations

import contextlib
import sys
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Key:
    name: str
    char: str = ""

    def __str__(self) -> str:
        return self.char if self.name == "char" else self.name


def _translate(ch: str) -> Key:
    if ch in ("\r", "\n"):
        return Key("enter")
    if ch in ("\x08", "\x7f"):
        return Key("backspace")
    if ch == "\t":
        return Key("tab")
    if ch == "\x1b":
        return Key("escape")
    code = ord(ch)
    if 1 <= code <= 26:
        return Key("ctrl+" + chr(code + 96))
    return Key("char", ch)


if sys.platform == "win32":
    import msvcrt

    _SCAN = {
        72: "up", 80: "down", 75: "left", 77: "right", 73: "pageup", 81: "pagedown",
        71: "home", 79: "end", 83: "delete",
        59: "f1", 60: "f2", 61: "f3", 62: "f4", 63: "f5", 64: "f6", 65: "f7", 66: "f8",
        67: "f9", 68: "f10", 133: "f11", 134: "f12",
        115: "ctrl+left", 116: "ctrl+right", 141: "ctrl+up", 145: "ctrl+down",
    }  # fmt: skip

    @contextlib.contextmanager
    def raw_mode():
        yield  # msvcrt already gives us unbuffered keys

    def poll_key(timeout: float) -> Key | None:
        deadline = time.monotonic() + timeout
        while True:
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\x00", "\xe0"):
                    code = ord(msvcrt.getwch())
                    return Key(_SCAN.get(code, f"scan{code}"))
                return _translate(ch)
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.01)

else:
    import select
    import termios
    import tty

    _SEQ = {
        "[A": "up", "[B": "down", "[C": "right", "[D": "left",
        "[5~": "pageup", "[6~": "pagedown", "[H": "home", "[F": "end", "[1~": "home", "[4~": "end",
        "[3~": "delete", "OP": "f1", "OQ": "f2", "OR": "f3", "OS": "f4",
        "[11~": "f1", "[12~": "f2", "[13~": "f3", "[14~": "f4", "[15~": "f5", "[17~": "f6",
        "[18~": "f7", "[19~": "f8", "[20~": "f9", "[21~": "f10", "[23~": "f11", "[24~": "f12",
        "[1;5A": "ctrl+up", "[1;5B": "ctrl+down", "[1;5C": "ctrl+right", "[1;5D": "ctrl+left",
    }  # fmt: skip

    @contextlib.contextmanager
    def raw_mode():
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            yield
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    def poll_key(timeout: float) -> Key | None:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if not ready:
            return None
        ch = sys.stdin.read(1)
        if ch != "\x1b":
            return _translate(ch)
        seq = ""
        while select.select([sys.stdin], [], [], 0.02)[0] and len(seq) < 6:
            seq += sys.stdin.read(1)
            if seq in _SEQ:
                return Key(_SEQ[seq])
        return Key("escape")
