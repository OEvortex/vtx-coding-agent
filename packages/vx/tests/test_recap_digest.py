"""The recap digest: prose kept, tool bodies cut to one line."""

from ctx.core.recap import (
    DIGEST_MAX_CHARS,
    DIGEST_TEXT_CHARS,
    RECENT_MESSAGE_WINDOW,
    build_recap_context,
    build_recap_digest,
)
from ctx.protocol.types import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)


def digest(messages) -> str:
    context = build_recap_digest(messages)
    assert len(context.messages) == 1
    content = context.messages[0].content
    assert isinstance(content, str)
    return content


def tool_step(name: str, call_id: str, arguments: dict, result: str, is_error: bool = False):
    return [
        AssistantMessage(
            content=[
                TextContent(text="Looking at the jobs module."),
                ToolCall(id=call_id, name=name, arguments=arguments),
            ]
        ),
        ToolResultMessage(
            tool_call_id=call_id,
            tool_name=name,
            content=[TextContent(text=result)],
            is_error=is_error,
        ),
    ]


class TestShape:
    def test_prose_survives_and_the_tool_body_does_not(self):
        body = "def kill_all(self):\n" * 500
        out = digest(
            [
                UserMessage(content="what does kill_all do in jobs.py?"),
                *tool_step("read", "c1", {"path": "packages/vx/src/vx/jobs.py"}, body),
            ]
        )
        assert "user: what does kill_all do in jobs.py?" in out
        assert "assistant: Looking at the jobs module." in out
        assert "def kill_all" in out  # the first line, as the result said
        assert out.count("def kill_all") == 1

    def test_a_call_names_the_file_it_touched(self):
        out = digest([*tool_step("read", "c1", {"path": "a/b.py", "offset": 1}, "one line")])
        assert "tool: read(a/b.py) -> one line" in out

    def test_an_error_result_says_so(self):
        out = digest([*tool_step("bash", "c1", {"command": "make"}, "boom", is_error=True)])
        assert "tool: bash(make) -> error: boom" in out

    def test_images_are_dropped(self):
        out = digest(
            [
                UserMessage(
                    content=[
                        TextContent(text="look at this"),
                        ImageContent(data="…", mime_type="image/png"),
                    ]
                )
            ]
        )
        assert "look at this" in out

    def test_a_call_without_arguments_still_prints(self):
        out = digest([*tool_step("todo", "c1", {}, "")])
        assert "tool: todo\n" in out or out.endswith("tool: todo")


class TestBudget:
    def test_a_long_session_is_cut_from_the_front_and_says_so(self):
        messages = []
        for index in range(60):
            messages.append(UserMessage(content=f"request {index} " + "x" * 300))
        out = digest(messages)
        assert out.startswith("Session so far (most recent last):\n(")
        assert "earlier entries omitted" in out
        assert len(out) <= DIGEST_MAX_CHARS + 200
        assert "request 59" in out
        assert "request 0 " not in out

    def test_only_the_tail_window_is_considered(self):
        messages = [UserMessage(content=f"early {i}") for i in range(RECENT_MESSAGE_WINDOW)]
        messages.append(UserMessage(content="the latest ask"))
        out = digest(messages)
        assert "the latest ask" in out

    def test_the_digest_is_far_smaller_than_the_transcript(self):
        body = "line of output\n" * 2000
        messages = [
            UserMessage(content="audit the parser"),
            *tool_step("read", "c1", {"path": "parser.py"}, body),
        ]
        small = sum(len(str(m)) for m in build_recap_digest(messages).messages)
        large = sum(len(str(m)) for m in build_recap_context(messages).messages)
        assert small * 5 < large

    def test_a_clipped_reply_ends_in_an_ellipsis(self):
        out = digest([AssistantMessage(content=[TextContent(text="word " * 400)])])
        assert "…" in out
        assert len(out) < DIGEST_TEXT_CHARS + 80  # plus the header line

    def test_nothing_worth_saying_leaves_the_context_empty(self):
        context = build_recap_digest([])
        assert context.messages == [] and context.broader_context is None
