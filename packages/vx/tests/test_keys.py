"""Key decoding: the sequences a terminal sends for the keys vx binds."""

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
