"""vx preferences file: history rows."""

import json

import pytest
from vx import prefs


@pytest.fixture
def prefs_file(tmp_path, monkeypatch):
    path = tmp_path / "vx-prefs.json"
    monkeypatch.setattr(prefs, "prefs_path", lambda: path)
    return path


class TestDefaults:
    def test_history_rows_default_zero(self, prefs_file):
        assert prefs.history_rows() == prefs.DEFAULT_HISTORY_ROWS == 0


class TestRoundTrip:
    def test_set_and_read_history_rows(self, prefs_file):
        prefs.set_history_rows(5000)
        assert prefs.history_rows() == 5000
        assert json.loads(prefs_file.read_text())["history_rows"] == 5000

    def test_negative_clamped_to_zero(self, prefs_file):
        prefs.set_history_rows(-5)
        assert prefs.history_rows() == 0

    def test_preserves_other_keys(self, prefs_file):
        prefs_file.write_text(json.dumps({"other": 1}))
        prefs.set_history_rows(100)
        data = json.loads(prefs_file.read_text())
        assert data["other"] == 1 and data["history_rows"] == 100


class TestCorrupt:
    def test_bad_json_returns_defaults(self, prefs_file):
        prefs_file.write_text("{not json")
        assert prefs.history_rows() == 0

    def test_non_dict_json_returns_defaults(self, prefs_file):
        prefs_file.write_text("[1,2]")
        assert prefs.history_rows() == 0

    def test_wrong_type_history_falls_back(self, prefs_file):
        prefs_file.write_text(json.dumps({"history_rows": "lots"}))
        assert prefs.history_rows() == 0


class TestLabel:
    def test_unlimited(self, prefs_file):
        assert prefs.history_label(0) == "unlimited"

    def test_formatted(self, prefs_file):
        assert prefs.history_label(5000) == "5,000 rows"
