"""Translate raw stdin bytes into key events.

Emits strings: printable chars, "enter", "esc", "backspace", "delete",
"up", "down", "left", "right", "pageup", "pagedown", "home", "end",
"ctrl+<char>", "alt+<char>", and "paste:<text>" for bracketed paste.
"""

from __future__ import annotations

import re
from time import monotonic

#: How long a lone ESC or a split UTF-8 prefix is held for the rest of its
#: sequence before it is emitted on its own. Terminals deliver a sequence's
#: bytes within a read or two, so 100 ms covers slow links without making the
#: Escape key feel laggy.
_GRACE = 0.1

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
    if base is None or "+" in base:
        # No known key, or a key that already carries a modifier of its own
        # (``shift+tab``): stacking another one would invent a key name.
        return None
    mod = int(match.group("mod"))
    if mod == 1:
        # ``CSI 5;1~`` is PageUp with an explicit "no modifiers" parameter;
        # some terminals send it that way and it must not be a distinct key.
        return base
    name = _MODIFIERS.get(mod)
    if name is None:
        return None
    return f"{name}+{base}"


def _decode_utf8(buf: bytes, pos: int) -> tuple[str | None, int]:
    """Decode one UTF-8 sequence at ``pos``. Returns (text, next_pos).

    ``text`` is None when ``buf`` ends mid-sequence: the bytes may be a
    character split across reads, so the caller waits for the rest rather
    than emitting a replacement character per fragment.
    """
    if pos >= len(buf):
        return None, pos
    b = buf[pos]
    if b < 0x80:
        return chr(b), pos + 1
    size = 2 if b < 0xE0 else 3 if b < 0xF0 else 4 if b < 0xF8 else 0
    if size == 0:
        return "\ufffd", pos + 1
    if pos + size > len(buf):
        return None, pos
    try:
        return buf[pos : pos + size].decode("utf-8"), pos + size
    except UnicodeDecodeError:
        return "\ufffd", pos + 1


def _utf8_prefix_len(buf: bytes) -> int:
    """Bytes at the start of ``buf`` forming an incomplete UTF-8 prefix."""
    if not buf or buf[0] < 0x80:
        return 0
    size = 2 if buf[0] < 0xE0 else 3 if buf[0] < 0xF0 else 4 if buf[0] < 0xF8 else 0
    if size == 0 or len(buf) >= size:
        return 0
    n = 1
    while n < len(buf) and 0x80 <= buf[n] < 0xC0:
        n += 1
    return n


class KeyDecoder:
    """Incremental decoder: feed bytes, get key events out.

    Input that may be the start of a split sequence — a lone ESC or an
    incomplete UTF-8 character — is held for ``_GRACE`` seconds. ``feed``
    resolves it as soon as the rest arrives; ``flush`` (call it on a timer)
    emits it on its own if nothing follows.
    """

    def __init__(self) -> None:
        self._buf = b""
        self._in_paste = False
        self._wait_since: float | None = None

    def feed(self, data: bytes) -> list[str]:
        events = self._expire()
        if data:
            self._buf += data
        events.extend(self._drain())
        return events

    def flush(self) -> list[str]:
        """Emit anything still waiting on the grace period, then parse on."""
        events = self._expire()
        events.extend(self._drain())
        return events

    def _expire(self) -> list[str]:
        if self._in_paste or self._wait_since is None:
            return []
        buf = self._buf
        lone_esc = buf == b"\x1b"
        utf8_len = 0 if lone_esc else _utf8_prefix_len(buf)
        if not lone_esc and utf8_len == 0:
            # Waiting on something that cannot be resolved by a timeout
            # (an incomplete CSI, say); drop the stale timestamp.
            self._wait_since = None
            return []
        if monotonic() - self._wait_since < _GRACE:
            return []
        self._wait_since = None
        if lone_esc:
            self._buf = b""
            return ["esc"]
        self._buf = buf[utf8_len:]
        return ["\ufffd"]

    def _drain(self) -> list[str]:
        events: list[str] = []
        while True:
            ev, consumed = self._parse_one()
            if ev is None:
                break
            if consumed:
                # Progress was made, so any wait timestamp belongs to a
                # fragment that no longer exists.
                self._wait_since = None
                if ev:
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
                # Lone ESC: probably the first byte of a sequence split across
                # reads. Hold it for the grace period instead of emitting a
                # phantom Escape; flush() emits "esc" if nothing follows.
                if self._wait_since is None:
                    self._wait_since = monotonic()
                return None, False
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
                if 0x30 <= buf[2] <= 0x39:
                    # Parameterised SS3 (``ESC O 1 ; 5 D``): same parameter
                    # shape as CSI, so the matcher works from index 2 as-is.
                    m = _match_csi(buf)
                    if m is None:
                        return None, False
                    seq, length = m
                    self._buf = buf[length:]
                    modified = _modified_key(seq)
                    if modified is not None:
                        return modified, True
                    return _CSI_KEYS.get(seq, f"ss3:{seq}"), True
                ch = chr(buf[2])
                self._buf = buf[3:]
                return _CSI_KEYS.get(ch, f"ss3:{ch}"), True
            # Alt+key: ESC followed by a printable char, control, or another
            # escape sequence (rxvt Meta+key is ESC + the normal sequence).
            self._buf = buf[1:]
            sub, _ = self._parse_one()
            if sub is None:
                return None, False
            if not sub:
                return "", True  # paste marker: consumed, nothing to emit
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
        if text is None:
            # Split multi-byte character: wait for the rest of it.
            if self._wait_since is None:
                self._wait_since = monotonic()
            return None, False
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
