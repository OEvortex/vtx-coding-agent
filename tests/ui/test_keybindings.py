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
    assert bindings["shift+tab"] == "cycle_permission_mode"
