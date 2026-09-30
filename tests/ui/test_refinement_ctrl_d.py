"""ctrl+d must do what a collapsed refinement block says it does.

The block renders "· ctrl+d for edits" while collapsed (`blocks.py`), but
`action_handle_ctrl_d` bailed out immediately unless the *session picker* was
open, so outside that picker the key did nothing — the hint was a lie, and a
user pressing it got silence (or, worse, a session-delete hint they never
asked for).

Precedence is deliberate: with the session picker open ctrl+d is still the
double-tap session delete, since the user is explicitly there. Only outside it
does the key mean "expand the refinement".
"""

from vtx.tui.blocks import RefinementBlock


class _Block(RefinementBlock):
    def __init__(self, edits, **kwargs):
        super().__init__(edits=edits, **kwargs)


def _block(edits=None, **kwargs):
    if edits is None:
        edits = [
            {
                "action": "update",
                "kind": "memory",
                "id": "note",
                "applied": True,
                "before": {"title": "old"},
                "after": {"title": "new", "content": "body"},
            }
        ]
    return _Block(edits, **kwargs)


def test_a_block_starts_collapsed_and_reports_details():
    block = _block()
    assert block.has_details is True
    assert block._expanded is False


def test_toggle_expands_and_collapses():
    block = _block()
    assert block.toggle_expanded() is True
    assert block._expanded is True
    assert block.toggle_expanded() is False
    assert block._expanded is False


def test_a_block_with_no_edits_has_nothing_to_expand():
    # A pass that applied nothing renders no hint, so there is nothing to do.
    assert _block(edits=[]).has_details is False


class _Chat:
    """Mirrors ChatLog.toggle_latest_refinement_expanded: True when it toggled."""

    def __init__(self, blocks):
        self._refinement_blocks = list(blocks)

    def toggle_latest_refinement_expanded(self):
        for block in reversed(self._refinement_blocks):
            if block.has_details:
                block.toggle_expanded()
                return True
        return False


def test_the_newest_collapsible_block_is_the_one_that_toggles():
    first, second = _block(), _block()
    chat = _Chat([first, second])

    assert chat.toggle_latest_refinement_expanded() is True
    assert second._expanded is True
    assert first._expanded is False


def test_pressing_again_collapses_it():
    block = _block()
    chat = _Chat([block])

    assert chat.toggle_latest_refinement_expanded() is True
    assert chat.toggle_latest_refinement_expanded() is True
    assert block._expanded is False


def test_with_no_collapsible_block_the_key_reports_no_toggle():
    # This is what stops ctrl+d falling through to the session-delete chord.
    assert _Chat([]).toggle_latest_refinement_expanded() is False
    assert _Chat([_block(edits=[])]).toggle_latest_refinement_expanded() is False
