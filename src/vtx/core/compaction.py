"""
Context compaction for long sessions.

When token usage exceeds a percentage of the context window, send the full
conversation to the LLM with a summarization prompt, then store the summary
as a CompactionEntry. The session.messages property filters to only show
messages after the compaction point.

Overflow formula:
    total_tokens >= (threshold_percent / 100) * context_window
"""

from vtx.core.abc import BaseProvider
from vtx.core.types import Message, TextPart, Usage, UserMessage

SUMMARIZATION_PROMPT = """You are producing a HANDOFF document for a coding session that is \
about to run out of context. Everything before this point is about to be deleted. \
Your summary is the ONLY thing the next agent will have.

That next agent will continue this work, not restart it. If you drop a detail, it \
cannot be recovered — it will be gone forever, and the next agent will either redo \
finished work or break something that already works.

## Binding length and fidelity rules

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
5. **Be exhaustive about state that is expensive to rediscover** — the current \
in-progress edit, the failing test, the flag that is set, the assumption that was \
verified. That is what the next agent cannot afford to lose.
6. Do not editorialize, apologize, or describe the conversation itself. Begin \
immediately with the first heading.

Use exactly these sections, in this order.

## 1. Objective & Constraints
- The user's goal, restated concretely enough to act on.
- Verbatim: any checklist, plan, spec, numbered requirements, or task rules the user gave.
- Every stated preference, constraint, and "do not do X" instruction.
- Acceptance criteria the user named, verbatim.

## 2. Environment & Runtime Facts
- Language/runtime versions, package installs, binaries added, env vars set \
(name + value if not secret).
- Anything that had to be installed, vendored, or worked around to run at all.
- Repo layout facts discovered: where the entry point is, how tests are invoked, \
the build/dev command, the CI command.

## 3. Architecture & Codebase Knowledge
- Module/class/function relationships the session established. Name them.
- Non-obvious behavior discovered by reading the code (inviants, ordering \
requirements, gotchas, why something works).
- Key algorithms, custom protocols, or state machines implemented — describe the \
actual logic, not the idea of it.

## 4. Interface Contracts & Key Code
- Every function/method signature created or modified — give the real signature, \
with types, verbatim in a fenced block.
- Every type, dataclass, pydantic model, schema, protocol, or API route added or changed.
- For each significant code change: the file path, the symbol, and the essence of \
the new logic (a few lines of the real code if that is the only way to be unambiguous).

## 5. Decisions & Rejected Alternatives
- Each significant choice, the options considered, and why this one won.
- **Dead ends, explicitly.** List every approach tried and abandoned, and why. \
This is what stops the next agent from repeating the same investigation. Include \
the failed attempts that "seemed promising".

## 6. Problems, Root Causes & Fixes
- Each bug: the exact error message or stack trace (verbatim, trimmed to the \
meaningful frames), the actual root cause, and the fix that resolved it.
- Problems still open, with what has been ruled out so far.

## 7. Files Touched
- Every file created, edited, or meaningfully read. Group by directory. One line \
per file: path — what it is for / what changed in it.
- Distinguish created vs edited vs read-only.

## 8. Commands Run & Outcomes
- Each significant command, what it did, and its result (pass/fail, key output, \
what it proved). Include the exact test/lint/build invocation used.
- Include any command that is expected to fail and why that is correct.

## 9. Current State (exact, right now)
- The precise state of the in-progress work: which file, which function, which \
line region, and what the next edit is meant to do.
- What currently compiles, what currently fails, and the exact failure if any.
- Test status right now: command run, pass/fail counts, which specific tests fail.
- Anything temporary, uncommitted, or half-applied. State it plainly.

## 10. Next Actions
- The immediate next 2-3 concrete steps, specific enough to execute without re-deriving them.
- Remaining TODO/checklist items, in order, with enough detail to act on each.
- Open questions the user still needs to answer, and what is blocked on them.

## 11. Do Not Redo
- Work that is already complete and verified. Explicitly, so it is not repeated.
- Investigations already performed and concluded, with the conclusion.
- Approaches that were tried and are known not to work.
---"""


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


def _calculate_context_tokens(usage: Usage) -> int:
    return (
        usage.input_tokens
        + usage.output_tokens
        + usage.cache_read_tokens
        + usage.cache_write_tokens
    )


async def generate_summary(
    messages: list[Message], provider: BaseProvider, system_prompt: str | None = None
) -> str:
    """Send the full conversation + summarization prompt to the LLM, return summary text."""
    summary_messages: list[Message] = [*messages, UserMessage(content=SUMMARIZATION_PROMPT)]

    stream = await provider.stream(summary_messages, system_prompt=system_prompt, tools=None)

    text_parts: list[str] = []
    async for part in stream:
        if isinstance(part, TextPart):
            text_parts.append(part.text)

    return "".join(text_parts)
