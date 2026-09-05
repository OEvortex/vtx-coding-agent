from vtx.coding_agent.config import Config, reset_config, set_config
from vtx.tui.widgets import CompactFooter


def test_footer_shows_auto_permission_mode_before_file_changes():
    set_config(Config({"permissions": {"mode": "auto"}}))
    try:
        footer = CompactFooter()
        footer._file_changes = {"a.txt": (2, 1)}
        _, _, line3 = footer._compute_lines()
    finally:
        reset_config()

    assert "✓ auto" in line3.plain
    assert "+2" in line3.plain
    assert "-1" in line3.plain


def test_footer_shows_prompt_permission_mode_without_file_changes():
    footer = CompactFooter()
    footer._permission_mode = "prompt"
    _, _, line3 = footer._compute_lines()

    assert "⏹ prompt" in line3.plain


def test_footer_updates_permission_mode_without_layout():
    footer = CompactFooter()
    footer.set_permission_mode("auto")
    assert footer._permission_mode == "auto"


def test_footer_updates_git_branch_without_layout():
    footer = CompactFooter()
    footer.set_git_branch("feature")
    assert footer._git_branch == "feature"
