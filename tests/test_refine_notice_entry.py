"""The refinement notice is structurally not a user turn.

Prime's `createRefinementNoticeMessage` builds a typed custom message rather
than a user turn. Stored as a plain `UserMessage` the refiner's own prose
("Record the current state... - create memory ...") read back as something the
user had asked for and was acted on.

Two properties have to hold together: the transcript must be able to tell the
notice apart from a user turn (it is a typed, non-displayed custom entry), and
the model must still receive it (`Session.messages` converts it back).
"""

from vtx.ai.agent.rlm.refine import REFINEMENT_NOTICE_TAG, append_refinement_notice, create_notice
from vtx.ai.agent.session import (
    REFINEMENT_NOTICE_CUSTOM_TYPE,
    CustomMessageEntry,
    MessageEntry,
    Session,
)

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


def _session_with_notice(tmp_path, source="user"):
    session = Session.in_memory(cwd=str(tmp_path))
    append_refinement_notice(
        session, create_notice(_RESULT, source), source=source, refinement_id="r1"
    )
    return session


def test_the_notice_is_a_typed_custom_entry_not_a_message(tmp_path):
    session = _session_with_notice(tmp_path)

    entries = [e for e in session.active_entries if isinstance(e, CustomMessageEntry)]
    assert [e.custom_type for e in entries] == [REFINEMENT_NOTICE_CUSTOM_TYPE]
    # A UserMessage would be indistinguishable from a turn the user typed.
    assert not [e for e in session.active_entries if isinstance(e, MessageEntry)]


def test_the_notice_is_not_rendered_in_the_transcript(tmp_path):
    session = _session_with_notice(tmp_path)

    entry = next(e for e in session.active_entries if isinstance(e, CustomMessageEntry))
    assert entry.display is False


def test_the_notice_still_reaches_the_model(tmp_path):
    # display=False hides it from the transcript, not from the provider.
    session = _session_with_notice(tmp_path)

    contents = [m.content for m in session.messages if hasattr(m, "content")]
    assert any(f"<{REFINEMENT_NOTICE_TAG}>" in c for c in contents)


def test_the_notice_survives_compaction(tmp_path):
    # The post-compaction branch builds its own list; a notice after the
    # compaction point must still be converted rather than dropped.
    from vtx.ai.agent.session import UserMessage

    session = Session.in_memory(cwd=str(tmp_path))
    session.append_message(UserMessage(content="work before compaction"))
    session.append_compaction(
        "a summary of earlier work",
        first_kept_entry_id=session.active_entries[0].id,
        tokens_before=100,
        tokens_after=10,
    )
    session.append_custom_message(
        REFINEMENT_NOTICE_CUSTOM_TYPE, create_notice(_RESULT, "auto"), display=False
    )

    assert any(
        f"<{REFINEMENT_NOTICE_TAG}>" in m.content
        for m in session.messages
        if hasattr(m, "content")
    )


def test_audit_only_custom_entries_stay_out_of_the_model_context(tmp_path):
    # The conversion must be narrow: revert/snapshot records are transcript
    # bookkeeping and must never reach the provider.
    session = Session.in_memory(cwd=str(tmp_path))
    session.append_custom_message("revert_state", "{}", display=False)

    assert session.messages == []


def test_the_notice_records_structured_details(tmp_path):
    session = Session.in_memory(cwd=str(tmp_path))
    append_refinement_notice(
        session,
        create_notice(_RESULT, "auto"),
        source="auto",
        refinement_id="refine_1",
        summary="Remember test preference",
        scope="local",
    )

    entry = next(e for e in session.active_entries if isinstance(e, CustomMessageEntry))
    assert entry.details["refinementId"] == "refine_1"
    assert entry.details["source"] == "auto"
    assert entry.details["summary"] == "Remember test preference"
