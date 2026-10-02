"""The compaction prompt is the only thing that survives context eviction.

A terse prompt here is silent and catastrophic: the conversation is deleted,
the summary is all the next agent gets, and a too-short summary means the next
agent redoes finished work or breaks working code. Regression reported as
800k tokens of history collapsing to a ~2k token summary.

These tests pin the prompt properties that produced the detailed output, so a
future rewrite cannot quietly restore the terse behaviour.
"""

from __future__ import annotations

import asyncio
import re
from typing import ClassVar

from vtx.ai.base import BaseProvider, LLMStream, ProviderConfig
from vtx.ai.sdk.openai import GenerationConfig
from vtx.ai.sdk.openai_responses import OpenAIResponsesSDK
from vtx.core.compaction import SUMMARIZATION_PROMPT, _strip_analysis, generate_summary
from vtx.protocol.types import StopReason, StreamDone, TextPart, UserMessage


def test_prompt_is_substantial():
    """A one-paragraph prompt cannot carry an 800k-token session."""
    assert len(SUMMARIZATION_PROMPT) > 3000, "compaction prompt collapsed to a stub"


def test_prompt_requires_using_the_output_budget():
    """The terse-output bug: the prompt must push for length explicitly."""
    lowered = SUMMARIZATION_PROMPT.lower()
    assert "output budget" in lowered or "budget remains" in lowered
    assert "thousand words" in lowered, "must scale length with session size"


def test_prompt_forbids_replacing_artifacts_with_descriptions():
    """The core fidelity rule: copy the artifact, not a description of it."""
    lowered = SUMMARIZATION_PROMPT.lower()
    assert "never replace a concrete artifact" in lowered
    assert "enumerate; do not merge" in lowered
    assert "verbatim" in lowered


def test_prompt_demands_concrete_capture():
    for token in ("signature", "error", "command", "path", "test name"):
        assert token in SUMMARIZATION_PROMPT.lower(), f"prompt never asks for {token}"


def test_prompt_keeps_the_sections_that_were_already_useful():
    """Original good sections must survive the rewrite."""
    headings = " ".join(re.findall(r"^## .+$", SUMMARIZATION_PROMPT, re.M)).lower()
    for concept in (
        "objective",
        "architecture",
        "decisions",
        "rejected alternatives",
        "root cause",
        "next action",
    ):
        assert concept in headings, f"lost the {concept} section"


def test_prompt_adds_the_sections_compaction_needed():
    """Sections the terse prompt never had, and that cost the most to lose."""
    headings = " ".join(re.findall(r"^## .+$", SUMMARIZATION_PROMPT, re.M)).lower()
    for concept in (
        "environment",  # versions/binaries/env vars
        "interface contracts",  # exact signatures
        "files touched",  # created vs edited vs read
        "commands run",  # what was run and what it proved
        "current state",  # the in-progress edit
        "do not redo",  # already-finished work and dead ends
    ):
        assert concept in headings, f"missing the {concept} section"


def test_prompt_warns_against_doing_completed_work_again():
    """The user's stated failure: the model forgets what it already did."""
    lowered = SUMMARIZATION_PROMPT.lower()
    assert "will continue this work, not restart it" in lowered
    assert "already complete" in lowered
    assert "repeating the same investigation" in lowered


def test_prompt_has_no_draft_pass():
    """The analysis scratchpad was pure latency: every token in it was generated,
    paid for, then deleted before the next agent saw it. On a long session that
    roughly doubled compaction wall-clock for nothing."""
    lowered = SUMMARIZATION_PROMPT.lower()
    assert "<analysis>" not in lowered
    assert "</analysis>" not in lowered
    # No mandated draft-then-write ceremony, and no ordering to wait for.
    assert "two blocks" not in lowered
    assert "do not write the handoff yet" not in lowered
    # The handoff is written directly, in one pass.
    assert "produce the handoff document directly" in lowered


def test_prompt_keeps_every_user_message():
    """User corrections are the detail a summary drops first."""
    headings = " ".join(re.findall(r"^## .+$", SUMMARIZATION_PROMPT, re.M)).lower()
    assert "all user messages" in headings
    assert "not a tool result" in SUMMARIZATION_PROMPT


def test_prompt_wants_the_code_not_its_location():
    lowered = SUMMARIZATION_PROMPT.lower()
    assert "not just its location" in lowered
    assert "quote the pivot point verbatim" in lowered


def test_strip_analysis_keeps_only_the_summary():
    raw = (
        "<analysis>\nwalked the whole session, noted 40 details\n</analysis>\n\n"
        "<summary>\n1. Objective: ship the parser\n\n2. Next: run the tests\n</summary>"
    )
    result = _strip_analysis(raw)
    assert "walked the whole session" not in result
    assert "<analysis>" not in result
    assert "<summary>" not in result
    assert result.startswith("1. Objective: ship the parser")
    assert result.endswith("2. Next: run the tests")


def test_strip_analysis_drops_an_unterminated_draft():
    """A draft cut off mid-stream used to survive whole: the regex needs a closing
    tag, so a truncated analysis stayed in the stored summary and was re-sent on
    every later request."""
    raw = "<analysis>\nwalked the whole session, noted 40 details\n## 1. Objective\n"
    result = _strip_analysis(raw)
    assert "walked the whole session" not in result
    assert "<analysis>" not in result


def test_strip_analysis_leaves_plain_summaries_alone():
    plain = "1. Objective: fix the bug\n\n2. Files touched:\n- src/a.py"
    assert _strip_analysis(plain) == plain


def test_generate_summary_disables_thinking_and_tools():
    """Compaction is a write-only task run over the whole history. Reasoning
    tokens buy nothing here and tools must never be offered, or the summarizer
    would start re-reading the codebase instead of writing the handoff."""
    captured: dict[str, object] = {}

    class _SpyProvider(BaseProvider):
        name = "spy"
        thinking_levels: ClassVar[list[str]] = ["default"]

        def __init__(self) -> None:
            self.config = ProviderConfig(model="spy", thinking_level="high")

        async def _stream_impl(
            self,
            messages,
            *,
            system_prompt=None,
            tools=None,
            temperature=None,
            max_tokens=None,
            thinking_level=None,
        ):
            captured["tools"] = tools
            captured["thinking_level"] = thinking_level
            stream = LLMStream()

            async def _iter():
                yield TextPart(text="1. Objective: ship it")
                yield StreamDone(stop_reason=StopReason.STOP)

            stream.set_iterator(_iter())
            return stream

        def should_retry_for_error(self, error: Exception) -> bool:
            return False

    summary = asyncio.run(generate_summary([UserMessage(content="hi")], _SpyProvider()))

    assert captured["tools"] is None
    assert captured["thinking_level"] == "off"
    assert summary == "1. Objective: ship it"


def test_off_never_clamps_back_into_a_reasoning_effort():
    """A model whose catalog marks ``off`` unsupported used to clamp it up to the
    nearest real effort, re-enabling reasoning for a caller that asked for none."""
    sdk = OpenAIResponsesSDK(api_key="test-key")
    config = GenerationConfig(
        model="m",
        thinking_level="off",
        thinking_level_map={"off": None, "low": "low", "high": "high"},
    )
    assert sdk._resolve_effort(config) is None
