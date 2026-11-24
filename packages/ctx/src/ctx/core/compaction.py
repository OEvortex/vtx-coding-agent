"""
Context compaction for long sessions.

When token usage exceeds a percentage of the context window, send the full
conversation to the LLM with a summarization prompt, then store the summary
as a CompactionEntry. The session.messages property filters to only show
messages after the compaction point.

Overflow formula:
    total_tokens >= (threshold_percent / 100) * context_window
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from ctx.protocol.abc import BaseProvider
from ctx.protocol.types import (
    ImageContent,
    Message,
    TextContent,
    TextPart,
    ToolResultMessage,
    Usage,
    UserMessage,
)

SUMMARIZATION_PROMPT = """You are producing a HANDOFF document for a coding session that is \
about to run out of context. The bulk of the conversation is about to be deleted. \
The next agent will also see the most recent turns verbatim, so do not spend the \
budget reciting them: summarize the earlier conversation and the state it left behind, \
and use those recent turns only to anchor what is still in progress.

That next agent will continue this work, not restart it. If you drop a detail from \
the earlier conversation, it cannot be recovered — it will be gone forever, and the \
next agent will either redo finished work or break something that already works.

Respond with plain text only. Do not call any tool.

## Output shape

Produce the handoff document directly, as plain text in the section format \
below. There is no scratchpad, no draft pass, and no planning block: every \
token you emit is a token the next agent has to read, so write the handoff \
once, well, and stop.

## Binding length and fidelity rules

0. **Your output budget is roughly 20,000 tokens.** Do not stop at 3,000. A \
200,000-token conversation warrants a handoff of several thousand tokens across \
all twelve sections. Fill the budget with concrete detail before you stop.

1. **Use the whole output budget.** This is the single most important rule. Do not \
stop while budget remains. A long session of 100k+ tokens of history warrants a \
summary of several thousand words, not a few hundred. After you finish a section, \
look for more concrete detail you left out and add it.
2. **Never replace a concrete artifact with a description of it.** Copy the real \
thing: exact file paths, exact symbol and function names, exact signatures, exact \
command lines, exact error text, exact config values, exact test names.
   - Bad: "several tests were added for the parser."
   - Good: "Added `tests/test_parser.py::test_rejects_trailing_comma` and \
`test_allows_nested_brackets`; both pass."
3. **Enumerate; do not merge.** If 12 files were touched, list all 12. If 4 bugs were \
fixed, list all 4. If a function's signature changed, give every changed signature. \
Collapsing distinct facts into one vague sentence is the failure mode you are \
being asked to avoid.
4. **Prefer structure over prose.** Bullets, fenced code, and tables survive \
compaction intact. Do not write paragraphs of narrative.
5. **Include the code, not just its location.** For anything created or changed, \
paste the real snippet — the function body, the config block, the schema, the test. \
A path plus a paraphrase of what changed there is worth far less than the diff \
itself, and the next agent will not open the file to find out.
6. **Be exhaustive about state that is expensive to rediscover** — the current \
in-progress edit, the failing test, the flag that is set, the assumption that was \
verified. That is what the next agent cannot afford to lose.
7. **Quote the pivot point verbatim.** Where you left off and what the user last \
asked for get quoted word for word, not paraphrased.
8. Do not editorialize, apologize, or describe the conversation itself. Begin \
immediately with section 1.

Use exactly these sections, in this order.

## 1. Objective & Constraints
- The user's goal, restated concretely enough to act on.
- Verbatim: any checklist, plan, spec, numbered requirements, or task rules the user gave.
- Every stated preference, constraint, and "do not do X" instruction.
- Acceptance criteria the user named, verbatim.
- If the conversation carries a compaction focus instruction, honor it — but never \
at the cost of dropping any section below.

## 2. All User Messages
- Every user message that is not a tool result, in the order it arrived.
- Quote each one verbatim. Do not summarize them, do not merge two into one line, \
do not drop the ones that look routine.
- Mark corrections prominently: when the user told you to do something differently, \
what they said, and what changed as a result.

## 3. Environment & Runtime Facts
- Language/runtime versions, package installs, binaries added, env vars set \
(name + value if not secret).
- Anything that had to be installed, vendored, or worked around to run at all.
- Repo layout facts discovered: where the entry point is, how tests are invoked, \
the build/dev command, the CI command.

## 4. Architecture & Codebase Knowledge
- Module/class/function relationships the session established. Name them.
- Non-obvious behavior discovered by reading the code (inviants, ordering \
requirements, gotchas, why something works).
- Key algorithms, custom protocols, or state machines implemented — describe the \
actual logic, not the idea of it.

## 5. Interface Contracts & Key Code
- Every function/method signature created or modified — give the real signature, \
with types, verbatim in a fenced block.
- Every type, dataclass, pydantic model, schema, protocol, or API route added or changed.
- For each significant code change: the file path, the symbol, the essence of the \
new logic, and the real code in a fenced block.

## 6. Decisions & Rejected Alternatives
- Each significant choice, the options considered, and why this one won.
- **Dead ends, explicitly.** List every approach tried and abandoned, and why. \
This is what stops the next agent from repeating the same investigation. Include \
the failed attempts that "seemed promising".

## 7. Problems, Root Causes & Fixes
- Each bug: the exact error message or stack trace (verbatim, trimmed to the \
meaningful frames), the actual root cause, and the fix that resolved it.
- Problems still open, with what has been ruled out so far.

## 8. Files Touched
- Every file created, edited, or meaningfully read. Group by directory. One line \
per file: path — what it is for / what changed in it, and why it matters to this work.
- Distinguish created vs edited vs read-only.
- For created or edited files, include the changed code.

## 9. Commands Run & Outcomes
- Each significant command, what it did, and its result (pass/fail, key output, \
what it proved). Include the exact test/lint/build invocation used.
- Include any command that is expected to fail and why that is correct.

## 10. Current State (exact, right now)
- The precise state of the in-progress work: which file, which function, which \
line region, and what the next edit is meant to do.
- What currently compiles, what currently fails, and the exact failure if any.
- Test status right now: command run, pass/fail counts, which specific tests fail.
- Anything temporary, uncommitted, or half-applied. State it plainly.

## 11. Next Actions
- The immediate next 2-3 concrete steps, specific enough to execute without re-deriving them.
- Remaining TODO/checklist items, in order, with enough detail to act on each.
- Open questions the user still needs to answer, and what is blocked on them.
- Anchor the handoff with verbatim quotes from the most recent exchange: the last \
instruction you were following and the exact point you stopped at.

## 12. Do Not Redo
- Work that is already complete and verified. Explicitly, so it is not repeated.
- Investigations already performed and concluded, with the conclusion.
- Approaches that were tried and are known not to work.
---"""

# The prompt mandates these headings in this order. Parsing them back out of the
# prompt keeps the UI checklist in lockstep with the prompt instead of drifting
# against a hand-copied list. Used to render live progress while the summary
# streams: each heading that appears is one section finished.
SUMMARY_SECTIONS: tuple[tuple[int, str], ...] = tuple(
    (int(num), title)
    for num, title in re.findall(r"^## (\d+)\. (.+)$", SUMMARIZATION_PROMPT, re.M)
)

_SECTION_HEADING_RE = re.compile(r"^##\s+(\d+)\.\s+(.+)$", re.M)


def summary_progress(text: str) -> list[tuple[int, str]]:
    """Return the ``(number, title)`` sections started so far in a partial stream.

    Older prompts wrapped the handoff in ``<summary>`` after an ``<analysis>``
    draft. The draft is gone, but a model may still emit the wrapper, so skip
    anything before it to keep the checklist honest.
    """
    summary = text
    if "<summary>" in text:
        summary = text.split("<summary>", 1)[1]
    return [(int(n), t.strip()) for n, t in _SECTION_HEADING_RE.findall(summary)]


@dataclass
class SummaryProgress:
    """Progress snapshot handed to ``generate_summary``'s ``on_delta`` callback."""

    chars: int
    sections_started: list[tuple[int, str]]


def is_overflow(usage: Usage, context_window: int, threshold_percent: float) -> bool:
    if context_window <= 0:
        return False
    count = (
        usage.input_tokens
        + usage.output_tokens
        + usage.cache_read_tokens
        + usage.cache_write_tokens
    )
    return count >= (threshold_percent / 100.0) * context_window


_ANALYSIS_RE = re.compile(r"<analysis>.*?</analysis>", re.DOTALL)


def _strip_analysis(text: str) -> str:
    """Normalize legacy tag wrappers out of a summary.

    The prompt no longer asks for an ``<analysis>`` draft pass, so this is a
    safety net for summaries that still carry the old ceremony (an in-flight
    session, or a model that volunteers the tags). An unterminated ``<analysis>``
    is cut to its end rather than kept whole — the draft is never wanted, and
    leaking it into stored context re-sends it on every later request.
    """
    stripped = _ANALYSIS_RE.sub("", text)
    if "<analysis>" in stripped:
        stripped = stripped.split("<analysis>", 1)[0]
    stripped = stripped.strip()
    if "<summary>" in stripped and "</summary>" in stripped:
        stripped = stripped.split("<summary>", 1)[1].rsplit("</summary>", 1)[0].strip()
    return re.sub(r"\n{3,}", "\n\n", stripped)


CLEARED_TOOL_RESULT = "[Old tool result content cleared]"

#: Results whose raw output is safe to clear before summarization. openclaude
#: keeps this exact set (plus MCP tools) in its microcompact step.
COMPACTABLE_TOOLS = frozenset(
    {
        "read",
        "bash",
        "shell",
        "grep",
        "find",
        "glob",
        "ls",
        "web_search",
        "web_fetch",
        "edit",
        "write",
        "apply_patch",
    }
)


def rough_token_count(text: str) -> int:
    """Char/4 estimate used where no provider usage is available."""
    return len(text) // 4


def estimate_messages_tokens(messages: Sequence[Message]) -> int:
    total = 0
    for message in messages:
        content = getattr(message, "content", None)
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for part in content:
                text = getattr(part, "text", None)
                if isinstance(text, str):
                    total += len(text)
                thinking = getattr(part, "thinking", None)
                if isinstance(thinking, str):
                    total += len(thinking)
    return total // 4


def _without_images(message: Message) -> Message:
    content = getattr(message, "content", None)
    if not isinstance(content, list) or not any(
        isinstance(part, ImageContent) for part in content
    ):
        return message
    parts: list[Any] = [
        TextContent(text="[image]") if isinstance(part, ImageContent) else part for part in content
    ]
    return message.model_copy(update={"content": parts})


def prepare_summary_messages(
    messages: Sequence[Message], *, prune_tool_results: bool = True, tool_results_to_keep: int = 3
) -> list[Message]:
    """Trim what the summarizer sees: images out, old tool results cleared.

    The tool call itself stays, so the model still knows what ran; only the raw
    result is replaced with a marker. The last few results stay intact so the
    summary can quote active state accurately.
    """
    compactable = [
        message
        for message in messages
        if isinstance(message, ToolResultMessage)
        and message.tool_name.lower() in COMPACTABLE_TOOLS
    ]
    keep = {id(message) for message in compactable[-max(1, tool_results_to_keep) :]}
    out: list[Message] = []
    for message in messages:
        if (
            isinstance(message, ToolResultMessage)
            and prune_tool_results
            and id(message) not in keep
        ):
            out.append(
                message.model_copy(
                    update={
                        "content": [TextContent(text=CLEARED_TOOL_RESULT)],
                        "ui_details": None,
                        "ui_details_full": None,
                        "ui_data": None,
                    }
                )
            )
        else:
            out.append(_without_images(message))
    return out


def _has_text(message: Message) -> bool:
    if isinstance(message, ToolResultMessage):
        return False
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        return any(isinstance(part, TextContent) and bool(part.text.strip()) for part in content)
    return False


def select_preserved_tail(
    messages: Sequence[Message], *, min_tokens: int, max_tokens: int, min_text_messages: int
) -> list[Message]:
    """Pick the recent tail kept verbatim beside the summary.

    Mirrors openclaude's session-memory compaction: expand backwards until both
    the token floor and the minimum number of real text turns are met, cap at a
    hard maximum, and never start on an orphaned tool result.
    """
    if not messages:
        return []
    start = len(messages)
    total = 0
    texts = 0
    while start > 0:
        message = messages[start - 1]
        total += estimate_messages_tokens([message])
        if _has_text(message):
            texts += 1
        start -= 1
        if (total >= min_tokens and texts >= min_text_messages) or total >= max_tokens:
            break
    while start > 0 and isinstance(messages[start], ToolResultMessage):
        start -= 1
    return list(messages[start:])


async def generate_summary(
    messages: list[Message],
    provider: BaseProvider,
    system_prompt: str | None = None,
    on_delta: Callable[[SummaryProgress], None] | None = None,
    focus_instructions: str | None = None,
    max_tokens: int | None = None,
) -> str:
    """Send the full conversation + summarization prompt to the LLM, return summary text.

    ``on_delta`` is called with a :class:`SummaryProgress` snapshot as tokens
    arrive so a UI can show the summary being written instead of an opaque
    wait. ``focus_instructions`` is prepended as a steering hint; the
    summarization prompt still goes last so it stays the operative instruction.

    The call pins ``thinking_level="off"``: reasoning tokens on a write-only
    task are pure latency, and the user's session reasoning level is meant for
    turns that actually act on the codebase. ``max_tokens`` should be an
    explicit budget (20k by default in the harness) — inheriting the provider's
    4096 default silently truncates a handoff after a few thousand tokens.
    """
    summary_messages: list[Message] = list(messages)
    if focus_instructions and focus_instructions.strip():
        summary_messages.append(
            UserMessage(
                content=f"Compaction focus requested by the kernel:\n{focus_instructions.strip()}"
            )
        )
    summary_messages.append(UserMessage(content=SUMMARIZATION_PROMPT))

    stream = await provider.stream(
        summary_messages,
        system_prompt=system_prompt,
        tools=None,
        thinking_level="off",
        max_tokens=max_tokens,
    )

    text_parts: list[str] = []
    chars = 0
    sections: list[tuple[int, str]] = []
    async for part in stream:
        if isinstance(part, TextPart):
            text_parts.append(part.text)
            if on_delta is None:
                continue
            chars += len(part.text)
            # Re-scan only when a delta can possibly carry a heading, so the
            # progress hook stays O(n) instead of re-joining and re-regexing
            # the whole buffer on every token.
            if "##" in part.text:
                found = summary_progress("".join(text_parts))
                if found:
                    sections = found
            on_delta(SummaryProgress(chars=chars, sections_started=sections))

    return _strip_analysis("".join(text_parts))
