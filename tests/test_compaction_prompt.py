"""The compaction prompt is the only thing that survives context eviction.

A terse prompt here is silent and catastrophic: the conversation is deleted,
the summary is all the next agent gets, and a too-short summary means the next
agent redoes finished work or breaks working code. Regression reported as
800k tokens of history collapsing to a ~2k token summary.

These tests pin the prompt properties that produced the detailed output, so a
future rewrite cannot quietly restore the terse behaviour.
"""

from __future__ import annotations

import re

from vtx.core.compaction import SUMMARIZATION_PROMPT, _strip_analysis


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


def test_prompt_drafts_before_summarizing():
    """The analysis pass is what stops detail dying between read and write."""
    assert "<analysis>" in SUMMARIZATION_PROMPT
    assert "<summary>" in SUMMARIZATION_PROMPT
    assert SUMMARIZATION_PROMPT.index("<analysis>") < SUMMARIZATION_PROMPT.index("<summary>")
    assert "chronologically" in SUMMARIZATION_PROMPT.lower()


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


def test_strip_analysis_leaves_plain_summaries_alone():
    plain = "1. Objective: fix the bug\n\n2. Files touched:\n- src/a.py"
    assert _strip_analysis(plain) == plain
