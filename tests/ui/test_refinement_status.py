"""The /refine status line must agree with the block it just rendered.

A pass whose edits were all rejected renders a block titled "Harness
refinement failed" and then, in the same breath, put "Refinement complete" in
the status bar. The two read as a contradiction because they are one, reported
twice: `applied`/`total` were being rendered by the block but ignored by the
status.

`applied == 0` is reached routinely — `validate_edit` rejects an edit missing
its `kind` ("unsupported kind None"), and the real session log had exactly
that — so this is the common failure, not an edge case.
"""

import types

from vtx.tui.commands.harness import _refinement_status


def _outcome(applied, total, rollback_of=None):
    return types.SimpleNamespace(applied=applied, total=total, rollback_of=rollback_of)


def test_a_fully_applied_pass_says_complete():
    assert "complete" in _refinement_status(_outcome(2, 2))


def test_a_fully_rejected_pass_does_not_say_complete():
    # The reported case: block says "Harness refinement failed".
    status = _refinement_status(_outcome(0, 1))
    assert "failed" in status
    assert "complete" not in status


def test_a_partially_applied_pass_is_called_out():
    status = _refinement_status(_outcome(1, 3))
    assert "1/3" in status
    assert "complete" not in status


def test_a_pass_with_nothing_proposed_is_unchanged_not_failed():
    status = _refinement_status(_outcome(0, 0))
    assert "unchanged" in status
    assert "failed" not in status


def test_a_single_applied_edit_is_not_pluralised():
    assert "1 edit applied" in _refinement_status(_outcome(1, 1))


def test_a_rollback_is_named_as_one():
    rollback = _outcome(0, 1, rollback_of="refine_123")
    status = _refinement_status(rollback)
    assert "rollback" in status
    assert "failed" in status


def test_a_missing_outcome_shape_does_not_raise():
    # Defensive: the status is cosmetic and must never break the command.
    assert _refinement_status(types.SimpleNamespace())
