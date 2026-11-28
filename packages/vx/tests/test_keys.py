"""Key decoding: the sequences a terminal sends for the keys vx binds."""

import pytest
import vx.tui.keys as keys
from vx.tui.keys import KeyDecoder


def decode(*seqs: bytes) -> list[str]:
    decoder = KeyDecoder()
    out: list[str] = []
    for seq in seqs:
        out.extend(decoder.feed(seq))
    return out


class TestPageKeys:
    def test_bare_page_keys(self):
        assert decode(b"\x1b[5~") == ["pageup"]
        assert decode(b"\x1b[6~") == ["pagedown"]

    def test_explicit_no_modifier_parameter(self):
        # Some terminals spell PageUp as CSI 5;1~ rather than CSI 5~.
        assert decode(b"\x1b[5;1~") == ["pageup"]
        assert decode(b"\x1b[6;1~") == ["pagedown"]

    def test_modified_page_keys(self):
        assert decode(b"\x1b[5;2~") == ["shift+pageup"]
        assert decode(b"\x1b[5;5~") == ["ctrl+pageup"]
        assert decode(b"\x1b[6;5~") == ["ctrl+pagedown"]

    def test_split_sequence_is_buffered(self):
        assert decode(b"\x1b[5", b"~") == ["pageup"]


@pytest.fixture
def clock(monkeypatch):
    """A controllable monotonic clock for the decoder's grace period."""
    now = [1000.0]
    monkeypatch.setattr(keys, "monotonic", lambda: now[0])
    return now


def feed_all(*seqs: bytes) -> list[str]:
    decoder = KeyDecoder()
    out: list[str] = []
    for seq in seqs:
        out.extend(decoder.feed(seq))
    return out


class TestSplitEscape:
    def test_csi_split_after_esc_has_no_phantom(self, clock):
        # ESC arrives at the end of one read; the rest of the CSI in the next.
        decoder = KeyDecoder()
        assert decoder.feed(b"\x1b") == []
        assert decoder.feed(b"[5~") == ["pageup"]

    def test_every_byte_of_a_csi_can_arrive_separately(self, clock):
        assert feed_all(b"\x1b", b"[", b"1", b";", b"5", b"D") == ["ctrl+left"]

    def test_ss3_split_after_esc(self, clock):
        assert feed_all(b"\x1b", b"O", b"A") == ["up"]

    def test_lone_escape_emitted_after_grace(self, clock):
        decoder = KeyDecoder()
        assert decoder.feed(b"\x1b") == []
        assert decoder.flush() == []  # still inside the grace period
        clock[0] += keys._GRACE + 0.01
        assert decoder.flush() == ["esc"]

    def test_escape_followed_by_key_after_grace(self, clock):
        decoder = KeyDecoder()
        decoder.feed(b"\x1b")
        clock[0] += keys._GRACE + 0.01
        assert decoder.feed(b"a") == ["esc", "a"]

    def test_escape_then_key_within_grace_is_alt(self, clock):
        # ESC + char inside the grace window is indistinguishable from Alt+key.
        assert feed_all(b"\x1b", b"a") == ["alt+a"]

    def test_double_escape_collapses_to_pending(self, clock):
        # ESC ESC: the second ESC stays pending; one "esc" after the grace.
        decoder = KeyDecoder()
        assert decoder.feed(b"\x1b\x1b") == []
        clock[0] += keys._GRACE + 0.01
        assert decoder.flush() == ["esc"]

    def test_esc_prefixed_sequence_is_alt_key(self, clock):
        # rxvt Meta+key: ESC + the normal sequence, e.g. Meta+PageUp.
        assert decode(b"\x1b\x1b[5~") == ["alt+pageup"]
        assert decode(b"\x1b\x1bOA") == ["alt+up"]


class TestSplitUtf8:
    def test_two_byte_char_split(self, clock):
        assert feed_all("é".encode()[:1], "é".encode()[1:]) == ["é"]

    def test_three_byte_char_split(self, clock):
        euro = "€".encode()
        assert feed_all(euro[:2], euro[2:]) == ["€"]

    def test_four_byte_char_split(self, clock):
        emoji = "\U0001f600".encode()
        assert feed_all(emoji[:1], emoji[1:3], emoji[3:]) == ["\U0001f600"]

    def test_truncated_char_expires_to_replacement(self, clock):
        decoder = KeyDecoder()
        assert decoder.feed("€".encode()[:2]) == []
        clock[0] += keys._GRACE + 0.01
        assert decoder.flush() == ["\ufffd"]

    def test_invalid_utf8_still_replacement(self):
        assert decode(b"\xff") == ["\ufffd"]
        assert decode(b"\xc3x") == ["\ufffd", "x"]


class TestModifiedSequences:
    def test_modified_csi_split(self, clock):
        assert feed_all(b"\x1b[1;", b"5D") == ["ctrl+left"]

    def test_parameterised_ss3(self, clock):
        # Some terminals spell modified arrows as ESC O <params> <final>.
        assert decode(b"\x1bO1;5D") == ["ctrl+left"]
        assert feed_all(b"\x1bO1;", b"5D") == ["ctrl+left"]
        assert decode(b"\x1bO5;5~") == ["ctrl+pageup"]
        assert decode(b"\x1bO5~") == ["pageup"]

    def test_parameterised_ss3_waits_for_final(self, clock):
        decoder = KeyDecoder()
        assert decoder.feed(b"\x1bO1;") == []
        assert decoder.feed(b"5D") == ["ctrl+left"]

    def test_modifier_on_already_modified_key_not_stacked(self, clock):
        # CSI 1;2Z would otherwise become "shift+shift+tab".
        assert decode(b"\x1b[1;2Z") == ["csi:1;2Z"]
