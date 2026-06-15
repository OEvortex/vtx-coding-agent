from vtx.ui.widgets import InfoBar


class _FakeLabel:
    """Minimal stand-in for the row2-left Label widget in click-detection tests."""

    def __init__(self, marker: str) -> None:
        self.marker = marker


def test_info_bar_does_not_treat_permission_mode_as_file_changes_click():
    info_bar = InfoBar("/tmp", "model")
    info_bar._file_changes = {"a.txt": (2, 1)}
    # Simulate having a real label without mounting the widget tree.
    label = _FakeLabel("permission-mode")
    info_bar.__dict__["_row2_left"] = label
    info_bar.__dict__["_file_changes_text_start"] = None
    info_bar._format_row2_left()

    # A widget that isn't the row2-left label should never be treated as a
    # file-changes click, regardless of x coordinate.
    other_widget = object()
    assert info_bar._is_file_changes_click(other_widget, 1) is False


def test_info_bar_treats_file_changes_text_as_file_changes_click():
    info_bar = InfoBar("/tmp", "model")
    info_bar._file_changes = {"a.txt": (2, 1)}
    label = _FakeLabel("file-changes")
    info_bar.__dict__["_row2_left"] = label
    info_bar.__dict__["_file_changes_text_start"] = None
    info_bar._format_row2_left()

    assert info_bar._file_changes_text_start is not None
    assert info_bar._is_file_changes_click(label, info_bar._file_changes_text_start) is True
