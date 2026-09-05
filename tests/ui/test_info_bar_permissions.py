from vtx.coding_agent.config import Config, reset_config, set_config
from vtx.tui.widgets import InfoBar


def test_footer_shows_auto_permission_mode_before_file_changes():
    set_config(Config({"permissions": {"mode": "auto"}}))
    try:
        footer = InfoBar(".", "model")
        footer._file_changes = {"a.txt": (2, 1)}
        line2 = footer._format_row2_left()
    finally:
        reset_config()

    assert "✓ auto" in line2.plain
    assert "+2" in line2.plain
    assert "-1" in line2.plain


def test_footer_shows_prompt_permission_mode_without_file_changes():
    footer = InfoBar(".", "model")
    footer._permission_mode = "prompt"
    line2 = footer._format_row2_left()

    assert "⏹ prompt" in line2.plain


def test_footer_updates_permission_mode_without_layout():
    footer = InfoBar(".", "model")
    footer.set_permission_mode("auto")
    assert footer._permission_mode == "auto"


def test_footer_updates_git_branch_without_layout():
    footer = InfoBar(".", "model")
    footer.set_git_branch("feature")
    assert footer._git_branch == "feature"
