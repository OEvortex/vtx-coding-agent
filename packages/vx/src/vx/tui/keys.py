"""Translate raw stdin bytes into key events.

Emits strings: printable chars, "enter", "esc", "backspace", "delete",
"up", "down", "left", "right", "pageup", "pagedown", "home", "end",
"ctrl+<char>", "alt+<char>", and "paste:<text>" for bracketed paste.
"""

from __future__ import annotations

import re

# CSI final bytes for special keys we care about.
_CSI_KEYS = {
    "A": "up",
    "B": "down",
    "C": "right",
    "D": "left",
    "H": "home",
    "F": "end",
    "Z": "shift+tab",
    "1~": "home",
    "4~": "end",
    "2~": "insert",
    "3~": "delete",
    "5~": "pageup",
    "6~": "pagedown",
}

_PASTE_START = b"\x1b[200~"
_PASTE_END = b"\x1b[201~"

#: CSI modifier parameters: 2 shift, 3 alt, 5 ctrl, and their sums.
_MODIFIERS = {
    2: "shift",
    3: "alt",
    4: "shift+alt",
    5: "ctrl",
    6: "shift+ctrl",
    7: "alt+ctrl",
    8: "shift+alt+ctrl",
}


#: A parameterised CSI: an optional code, an optional modifier, and a final.
_MODIFIED_CSI = re.compile(r"^(?P<code>\d*)(?:;(?P<mod>\d+))?(?P<final>[A-Za-z~])$")


def _modified_key(seq: str) -> str | None:
    """``1;5D`` -> ``ctrl+left``: a cursor key pressed with modifiers.

    Terminals report ctrl/alt/shift with a parameter before the final byte,
    such as ``ESC [ 1 ; 5 D`` for ctrl+left, so word-wise movement has to read
    the modifier rather than only the arrow.
    """
    match = _MODIFIED_CSI.match(seq)
    if match is None or not match.group("mod"):
        return None
    code = match.group("code")
    final = match.group("final")
    base = _CSI_KEYS.get(f"{code}{final}") or _CSI_KEYS.get(final)
    if base is None:
        return None
    name = _MODIFIERS.get(int(match.group("mod")))
    if name is None:
        return None
    return f"{name}+{base}"


def _decode_utf8(buf: bytes, pos: int) -> tuple[str | None, int]:
    """Decode one UTF-8 sequence at ``pos``. Returns (text, next_pos)."""
    if pos >= len(buf):
        return None, pos
    b = buf[pos]
    if b < 0x80:
        return chr(b), pos + 1
    for size in (2, 3, 4):
        if pos + size <= len(buf):
            try:
                return buf[pos : pos + size].decode("utf-8"), pos + size
            except UnicodeDecodeError:
                continue
    return "\ufffd", pos + 1


class KeyDecoder:
    """Incremental decoder: feed bytes, get key events out."""

    def __init__(self) -> None:
        self._buf = b""
        self._in_paste = False

    def feed(self, data: bytes) -> list[str]:
        if not data:
            return []
        self._buf += data
        events: list[str] = []
        while True:
            ev, consumed = self._parse_one()
            if ev is None:
                break
            if consumed and ev:
                events.append(ev)
        return events

    def _parse_one(self) -> tuple[str | None, bool]:
        buf = self._buf
        if not buf:
            return None, False

        if self._in_paste:
            end = buf.find(_PASTE_END)
            if end == -1:
                # Wait for the terminator; keep buffering.
                return None, False
            text = buf[:end]
            self._buf = buf[end + len(_PASTE_END) :]
            self._in_paste = False
            try:
                return "paste:" + text.decode("utf-8", errors="replace"), True
            except Exception:
                return "paste:", True

        if buf.startswith(_PASTE_START):
            self._buf = buf[len(_PASTE_START) :]
            self._in_paste = True
            return "", True  # marker event, ignored by callers filtering ""

        b = buf[0]

        # Escape sequences.
        if b == 0x1B:
            if len(buf) == 1:
                # Lone ESC: treat as Escape key.
                self._buf = b""
                return "esc", True
            nxt = buf[1]
            if nxt == 0x5B:  # '[' -> CSI
                m = _match_csi(buf)
                if m is None:
                    return None, False  # incomplete; wait for more bytes
                seq, length = m
                self._buf = buf[length:]
                modified = _modified_key(seq)
                if modified is not None:
                    return modified, True
                if seq.startswith("<"):
                    return _mouse_event(seq), True
                return _CSI_KEYS.get(seq, f"csi:{seq}"), True
            if nxt == 0x4F:  # 'O' -> SS3 (some terminals send ESC O A etc.)
                if len(buf) < 3:
                    return None, False
                ch = chr(buf[2])
                self._buf = buf[3:]
                return _CSI_KEYS.get(ch, f"ss3:{ch}"), True
            # Alt+key: ESC followed by a printable char or control.
            self._buf = buf[1:]
            sub, _ = self._parse_one()
            if sub is None:
                return None, False
            if len(sub) == 1 and ord(sub) >= 32:
                return f"alt+{sub}", True
            return f"alt+{sub}", True

        # Control characters.
        if b == 0x0D or b == 0x0A:
            self._buf = buf[1:]
            return "enter", True
        if b == 0x7F or b == 0x08:
            self._buf = buf[1:]
            return "backspace", True
        if b == 0x09:
            self._buf = buf[1:]
            return "tab", True
        if b < 0x20:
            self._buf = buf[1:]
            name = {0x03: "ctrl+c", 0x04: "ctrl+d", 0x1A: "ctrl+z"}.get(b)
            if name:
                return name, True
            return f"ctrl+{chr(b + 96)}", True

        # Printable / UTF-8.
        text, pos = _decode_utf8(buf, 0)
        self._buf = buf[pos:]
        return text, True


#: Mouse buttons as SGR reports them, by their low two bits.
_MOUSE_BUTTONS = {0: "left", 1: "middle", 2: "right"}


def _mouse_event(seq: str) -> str:
    """``<64;10;5M`` -> ``mouse:wheelup:10:5``.

    Wheel reports arrive as buttons 64/65 in the same stream as clicks, so the
    transcript can scroll under the pointer the way a pager would.
    """
    body = seq[1:-1]
    parts = body.split(";")
    if len(parts) != 3:
        return f"csi:{seq}"
    try:
        code, x, y = (int(part) for part in parts)
    except ValueError:
        return f"csi:{seq}"
    if code & 0x40:
        action = "wheelup" if code & 1 == 0 else "wheeldown"
    elif seq.endswith("m"):
        action = "release"
    else:
        action = _MOUSE_BUTTONS.get(code & 3, "click")
    if code & 32 and action not in ("wheelup", "wheeldown"):
        action = "move"
    return f"mouse:{action}:{x}:{y}"


def _match_csi(buf: bytes) -> tuple[str, int] | None:
    """Match ``ESC [ params final`` and return (param-string, total length)."""
    i = 2
    params = bytearray()
    while i < len(buf):
        c = buf[i]
        if 0x40 <= c <= 0x7E:  # final byte
            seq = params.decode("ascii", errors="replace") + chr(c)
            return seq, i + 1
        if 0x30 <= c <= 0x39 or c in (0x3B, 0x3C, 0x3F):
            params.append(c)
            i += 1
            continue
        # Modifier/other byte — bail.
        return "", i + 1
    return None
