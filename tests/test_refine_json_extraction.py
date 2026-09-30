"""The refiner's reply parsing.

Every refinement failure a user sees as "Refinement failed" arrives through
``extract_json_object``, so a formatting slip by the auxiliary model is a
visible outage of the continual harness. Two real ones:

- an invalid escape (a bare ``\\w`` in a regex, a Windows path) made balanced
  but invalid JSON, which was rejected outright;
- prose containing a brace ("the set {a,b}") was sliced into a candidate that
  could never parse, so a perfectly good proposal after the chatter was lost.
"""

import pytest

from vtx.ai.agent.rlm.refine import extract_json_object


def test_an_invalid_escape_is_repaired_not_rejected():
    # A regex in a memory title is the realistic source of a bare \w.
    text = '{"entries": [{"kind": "memory", "title": "use \\w for word boundaries"}]}'
    assert extract_json_object(text) == {
        "entries": [{"kind": "memory", "title": "use \\w for word boundaries"}]
    }


def test_a_windows_path_does_not_break_the_parse():
    assert extract_json_object('{"note": "run C:\\\\Users\\\\x"}') == {"note": "run C:\\Users\\x"}


def test_a_proposal_after_prose_is_found():
    text = 'The refiner proposes:\n\n{\n  "shouldRefine": true\n}'
    assert extract_json_object(text) == {"shouldRefine": True}


def test_prose_containing_a_brace_does_not_become_the_proposal():
    # {a,b} is balanced, so a naive first-brace slice returns it; json-repair
    # would even turn it into a list. The real object follows it.
    text = 'I considered the set {a,b}. Here: {"rationale": "ok"}'
    assert extract_json_object(text) == {"rationale": "ok"}


def test_only_the_first_object_of_several_is_taken():
    assert extract_json_object('first {"a": 1} then {"b": 2}') == {"a": 1}


def test_a_fenced_block_is_preferred():
    assert extract_json_object('prose\n```json\n{"a": 1}\n```\ntrailing') == {"a": 1}


def test_prose_with_no_object_is_still_an_error():
    with pytest.raises(ValueError, match="did not return a JSON object"):
        extract_json_object("This pass is not worth persisting.")


def test_truncation_is_reported_as_truncation():
    # Truncated output needs a bigger budget, not a repair.
    with pytest.raises(ValueError, match="truncated"):
        extract_json_object('{"rationale": "unterminated')
