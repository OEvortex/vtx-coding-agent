from textual.binding import Binding

from vtx.ui.app import Vtx


def _binding_key_and_action(binding) -> tuple[str, str]:
    if isinstance(binding, Binding):
        return binding.key, binding.action
    key, action, *_ = binding
    return key, action


def test_thinking_and_permission_mode_keybindings():
    bindings = dict(_binding_key_and_action(binding) for binding in Vtx.BINDINGS)

    assert bindings["ctrl+t"] == "cycle_thinking_level"
    assert bindings["ctrl+o"] == "toggle_tool_output"
    assert bindings["ctrl+shift+t"] == "toggle_thinking"
    # Shift+Tab cycles handoff agents; permission-mode cycling moved to
    # alt+ctrl+p (see the new test below). ctrl+shift+p is intentionally
    # left free for a future command palette.
    assert bindings["shift+tab"] == "cycle_agent"
    assert bindings["alt+ctrl+p"] == "cycle_permission_mode"
    assert "ctrl+shift+p" not in bindings


def test_agent_keybindings():
    bindings = dict(_binding_key_and_action(binding) for binding in Vtx.BINDINGS)
    # Shift+Tab is the canonical "cycle handoff agent" binding.
    assert bindings["shift+tab"] == "cycle_agent"
