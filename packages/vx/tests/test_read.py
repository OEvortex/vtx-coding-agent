"""Read tool: numbered output, ranges, and path formatting."""

import pytest
from vx.tools.read import PREVIEW_LINES, ReadParams, ReadTool, _strip_line_number


class TestStripLineNumber:
    def test_strips_gutter(self):
        assert _strip_line_number("     1\thello") == "hello"

    def test_no_tab_untouched(self):
        assert _strip_line_number("plain") == "plain"


class TestFormatCall:
    def test_plain_path(self, tmp_path):
        tool = ReadTool()
        assert tool.format_call(ReadParams(path=str(tmp_path / "a.py"))) == str(tmp_path / "a.py")

    def test_range_shows_bounds(self, tmp_path):
        tool = ReadTool()
        out = tool.format_call(ReadParams(path=str(tmp_path / "a.py"), offset=5, limit=10))
        assert ":5-14" in out


class TestReadFile:
    @pytest.mark.asyncio
    async def test_numbers_lines(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("one\ntwo\nthree\n")
        tool = ReadTool()
        content = await tool.read_file(f, None, None)
        assert "     1\tone" in content
        assert "     3\tthree" in content

    @pytest.mark.asyncio
    async def test_offset_and_limit(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("\n".join(f"l{i}" for i in range(20)) + "\n")
        tool = ReadTool()
        content = await tool.read_file(f, offset=5, limit=3)
        # Lines 5-7 (1-based): l4, l5, l6.
        assert "l3" not in content
        assert "     5\tl4" in content
        assert "     7\tl6" in content
        assert "l7" not in content

    @pytest.mark.asyncio
    async def test_long_line_truncated(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("x" * 3000 + "\n")
        tool = ReadTool()
        content = await tool.read_file(f, None, None)
        assert "truncated" in content


class TestExecute:
    @pytest.mark.asyncio
    async def test_missing_path(self, tmp_path):
        result = await ReadTool().execute(ReadParams(path=str(tmp_path / "nope")))
        assert not result.success

    @pytest.mark.asyncio
    async def test_reads_file(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("hello\n")
        result = await ReadTool().execute(ReadParams(path=str(f)))
        assert result.success
        assert "hello" in (result.result or "")
        ui = result.ui_data or {}
        assert ui["preview"] == "hello"

    @pytest.mark.asyncio
    async def test_paged_shows_all_as_preview(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("\n".join(f"l{i}" for i in range(PREVIEW_LINES + 5)) + "\n")
        result = await ReadTool().execute(ReadParams(path=str(f), offset=1, limit=50))
        assert result.success
        ui = result.ui_data or {}
        assert ui["paged"] is True
