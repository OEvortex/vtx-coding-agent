from vtx.agent.session import Session
from vtx.protocol.types import (
    AssistantMessage,
    FileChanges,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)


def test_session_token_totals_file_changes_and_message_counts() -> None:
    session = Session.in_memory()
    session.append_message(UserMessage(content="hello"))
    session.append_message(
        AssistantMessage(
            content=[ToolCall(id="t1", name="read", arguments={"path": "a.txt"})],
            usage=Usage(
                input_tokens=100, output_tokens=50, cache_read_tokens=10, cache_write_tokens=5
            ),
        )
    )
    session.append_message(
        ToolResultMessage(
            tool_call_id="t1",
            tool_name="read",
            content=[TextContent(text="content")],
            file_changes=FileChanges(path="a.txt", added=3, removed=1),
        )
    )
    session.append_message(
        AssistantMessage(
            content=[TextContent(text="done")],
            usage=Usage(
                input_tokens=20, output_tokens=30, cache_read_tokens=0, cache_write_tokens=2
            ),
        )
    )

    totals = session.token_totals()
    assert totals.input_tokens == 120
    assert totals.output_tokens == 80
    assert totals.cache_read_tokens == 10
    assert totals.cache_write_tokens == 7
    assert totals.context_tokens == 165  # peak across all assistant messages
    assert totals.total_tokens == 217

    assert session.file_changes_summary() == {"a.txt": (3, 1)}

    counts = session.message_counts()
    assert counts.user_messages == 1
    assert counts.assistant_messages == 2
    assert counts.tool_calls == 1
    assert counts.tool_results == 1
    assert counts.total_messages == 3


def test_session_created_at_is_exposed() -> None:
    session = Session.in_memory()
    assert session.created_at is not None


def _assistant(usage: Usage) -> AssistantMessage:
    return AssistantMessage(content=[TextContent(text="ok")], usage=usage)


def test_subagent_usage_is_charged_to_the_dispatching_session() -> None:
    """A sub-agent's tokens must land in the session that dispatched it.

    A sub-agent runs in its own persisted session, so its assistant messages
    never enter this transcript and ``token_totals`` - which reads only these
    entries - would report this session as if the sub-agent had cost nothing,
    while the lifetime total read from every session log included it.
    """
    session = Session.in_memory()
    session.append_message(UserMessage(content="delegate this"))
    session.append_message(_assistant(Usage(input_tokens=100, output_tokens=50)))

    session.record_subagent_usage(
        input_tokens=5000,
        output_tokens=2000,
        cache_read_tokens=300,
        cache_write_tokens=100,
        turns=4,
        label="find the auth bug",
    )

    totals = session.token_totals()
    assert totals.input_tokens == 5100
    assert totals.output_tokens == 2050
    assert totals.cache_read_tokens == 300
    assert totals.cache_write_tokens == 100


def test_subagent_usage_charge_is_not_a_turn_and_not_model_facing() -> None:
    """The charge is accounting, not conversation.

    It must not inflate the session's turn count - `/usage` reports the model's
    own turns beside it - and it must never reach the provider, so a sub-agent
    dispatch does not quietly grow the prompt.
    """
    session = Session.in_memory()
    session.append_message(UserMessage(content="delegate this"))
    session.append_message(_assistant(Usage(input_tokens=1, output_tokens=1)))
    before_turns = session.message_counts().assistant_messages
    before_messages = len(session.messages)

    session.record_subagent_usage(input_tokens=999, output_tokens=999, turns=7)

    assert session.message_counts().assistant_messages == before_turns
    assert len(session.messages) == before_messages


def test_subagent_usage_charge_of_zero_is_a_no_op() -> None:
    """Nothing spent means nothing to record, so no entry is added."""
    session = Session.in_memory()
    session.append_message(UserMessage(content="delegate this"))
    session.append_message(_assistant(Usage(input_tokens=1, output_tokens=1)))
    before = len(session.active_entries)

    session.record_subagent_usage(
        input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0
    )

    assert len(session.active_entries) == before
