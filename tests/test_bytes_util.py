import pytest

from vtx.core.bytes_util import format_bytes, parse_bytes


class TestFormatBytes:
    def test_zero_returns_bytes(self) -> None:
        assert format_bytes(0) == "0 B"

    def test_small_bytes(self) -> None:
        assert format_bytes(512) == "512 B"
        assert format_bytes(999) == "999 B"

    def test_kilobytes(self) -> None:
        assert format_bytes(1024) == "1 KB"
        assert format_bytes(1536) == "1.5 KB"
        assert format_bytes(2048) == "2 KB"

    def test_megabytes(self) -> None:
        assert format_bytes(1024**2) == "1 MB"
        assert format_bytes(int(1.5 * 1024**2)) == "1.5 MB"
        assert format_bytes(int(10.25 * 1024**2)) == "10.2 MB"

    def test_gigabytes(self) -> None:
        assert format_bytes(1024**3) == "1 GB"
        assert format_bytes(int(2.75 * 1024**3)) == "2.8 GB"

    def test_terabytes(self) -> None:
        assert format_bytes(1024**4) == "1 TB"

    def test_larger_units(self) -> None:
        assert format_bytes(1024**5) == "1 PB"
        assert format_bytes(1024**6) == "1 EB"

    def test_rounds_sensibly(self) -> None:
        assert format_bytes(1) == "1 B"
        assert format_bytes(1023) == "1023 B"
        assert format_bytes(1025) == "1 KB"

    def test_negative_raises(self) -> None:
        with pytest.raises(ValueError):
            format_bytes(-1)


class TestParseBytes:
    def test_bytes(self) -> None:
        assert parse_bytes("512 B") == 512
        assert parse_bytes("0 B") == 0

    def test_kilobytes(self) -> None:
        assert parse_bytes("1 KB") == 1024
        assert parse_bytes("1.5 KB") == int(1.5 * 1024)
        assert parse_bytes("2KB") == 2048

    def test_megabytes(self) -> None:
        assert parse_bytes("1 MB") == 1024**2
        assert parse_bytes("10.2 MB") == int(10.2 * 1024**2)

    def test_gigabytes(self) -> None:
        assert parse_bytes("1 GB") == 1024**3
        assert parse_bytes("2.75 GB") == int(2.75 * 1024**3)

    def test_terabytes(self) -> None:
        assert parse_bytes("1 TB") == 1024**4

    def test_larger_units(self) -> None:
        assert parse_bytes("1 PB") == 1024**5
        assert parse_bytes("1 EB") == 1024**6

    def test_case_insensitive_unit(self) -> None:
        assert parse_bytes("1 mb") == 1024**2
        assert parse_bytes("1.5 Mb") == int(1.5 * 1024**2)
        assert parse_bytes("2 gB") == 2 * 1024**3

    def test_whitespace(self) -> None:
        assert parse_bytes("  1 KB  ") == 1024
        assert parse_bytes("\t2MB\n") == 2 * 1024**2

    def test_roundtrip(self) -> None:
        for size in [0, 512, 1024, 1536, 1024**2, int(1.5 * 1024**2)]:
            formatted = format_bytes(size)
            assert parse_bytes(formatted) == size

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_bytes("")
        with pytest.raises(ValueError):
            parse_bytes("   ")

    def test_malformed_string_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_bytes("abc")
        with pytest.raises(ValueError):
            parse_bytes("1.2.3 KB")
        with pytest.raises(ValueError):
            parse_bytes("-1 KB")

    def test_unknown_unit_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_bytes("1 ZB")

    def test_negative_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_bytes("-1 KB")
