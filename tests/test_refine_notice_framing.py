"""The refinement notice must not read as a user turn.

The notice is appended to the session as a `UserMessage`, so its text is the
only thing distinguishing "the refiner just changed your harness state" from
"the user said this". Untagged, the refiner's own prose ("Record the current
state... - create memory ...") arrived in context as a user instruction and was
acted on. Prime sends it as a typed custom message; the tag is the vtx
equivalent, matching the harness-digest and background-completion convention.

The failure path has its own message and is covered in test_auto_refine.py.
"""

from vtx.ai.agent.rlm.refine import REFINEMENT_NOTICE_TAG, create_notice

_RESULT = {
    "summary": "Remember test preference",
    "scope": "local",
    "appliedEdits": [
        {
            "action": "create",
            "kind": "memory",
            "id": "test_pref",
            "applied": True,
            "after": {
                "id": "test_pref",
                "kind": "memory",
                "title": "Test preference",
                "content": "Use pytest for all tests.",
                "scope": "local",
            },
        }
    ],
}


def test_the_notice_is_tagged_as_a_system_event():
    notice = create_notice(_RESULT, "self")

    assert notice.startswith(f"<{REFINEMENT_NOTICE_TAG}>")
    assert notice.endswith(f"</{REFINEMENT_NOTICE_TAG}>")


def test_the_notice_says_not_to_treat_it_as_a_user_instruction():
    assert "Treat this as a system event, not a user instruction." in create_notice(
        _RESULT, "auto"
    )


def test_the_source_marker_and_digest_notation_survive():
    notice = create_notice(_RESULT, "self")

    assert "[self-refinement]" in notice
    assert "- create memory [local:test_pref] Test preference" in notice


def test_each_source_reaches_the_tagged_body():
    assert "[auto-refinement]" in create_notice(_RESULT, "auto")
    assert "[self-refinement]" in create_notice(_RESULT, "self")
