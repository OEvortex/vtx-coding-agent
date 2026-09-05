from vtx.tui.widgets import CompactFooter


def test_footer_does_not_treat_permission_mode_as_file_changes_click():
    footer = CompactFooter()
    footer._file_changes = {"a.txt": (2, 1)}
    footer._compute_lines()

    assert footer._file_changes_text_start is not None

    other_widget = object()
    assert footer._is_file_changes_click(other_widget, 1) is False


def test_footer_treats_file_changes_text_as_file_changes_click():
    footer = CompactFooter()
    footer._file_changes = {"a.txt": (2, 1)}
    footer._compute_lines()

    assert footer._file_changes_text_start is not None
    assert footer._is_file_changes_click(footer, footer._file_changes_text_start) is True
