from vtx import Config, reset_config, set_config
from vtx.ui.styles import get_styles


def test_approval_background_blends_terminal_bg_with_accent():
    set_config(Config({"ui": {"theme": "gruvbox-dark"}}))

    try:
        styles = get_styles()
    finally:
        reset_config()

    approval_block = styles.split(".tool-block.-approval {")[1].split("}", 1)[0]

    assert "background: #2d2e2e;" in approval_block
    assert "background: #3c3836;" not in approval_block


def test_completion_hidden_info_bar_collapses_fixed_row():
    styles = get_styles()

    info_bar_hidden_block = styles.split(".info-bar.-completion-hidden {")[1].split("}", 1)[0]

    assert "display: none;" in info_bar_hidden_block
    assert "height: 0;" in info_bar_hidden_block
