"""Tests for the skills catalog as refreshable session state.

The catalog in the system prompt is a snapshot. Without a refresh path, a skill
installed or deleted mid-session stays advertised for the rest of the run, and
rebuilding the system prompt to fix that invalidates the cached prefix on every
boundary. These pin the fingerprint, the update/removed wording, and the
one-listing-per-change shape — including that a rendered list is joined as a
string, since extending a list with one iterates it character by character.
"""

from vtx.ai.agent.skills_refresh import (
    SKILLS_REFRESH_TAG,
    SkillSummary,
    build_skills_refresh_message,
    fingerprint_summaries,
    format_summaries,
    is_skills_refresh_message,
    render_removed,
    render_update,
    summarize_skills,
)


def _skill(name: str, description: str, include: bool = True):
    return type(
        "S", (), {"name": name, "description": description, "include_in_prompt": include}
    )()


# =================================================================================================
# fingerprint
# =================================================================================================


def test_the_same_catalog_fingerprints_identically():
    a = summarize_skills([_skill("b", "second"), _skill("a", "first")])
    b = summarize_skills([_skill("a", "first"), _skill("b", "second")])

    assert fingerprint_summaries(a) == fingerprint_summaries(b)


def test_a_changed_description_changes_the_fingerprint():
    before = summarize_skills([_skill("review", "old")])
    after = summarize_skills([_skill("review", "new")])

    assert fingerprint_summaries(before) != fingerprint_summaries(after)


def test_an_added_or_removed_skill_changes_the_fingerprint():
    base = summarize_skills([_skill("review", "d")])

    assert fingerprint_summaries(base) != fingerprint_summaries(
        summarize_skills([_skill("review", "d"), _skill("new", "n")])
    )
    assert fingerprint_summaries(base) != fingerprint_summaries(summarize_skills([]))


def test_a_cmd_only_skill_is_not_part_of_the_prompt_catalog():
    """It is absent from the prompt, so its presence must not read as a change."""
    base = summarize_skills([_skill("review", "d")])
    with_cmd_only = summarize_skills([_skill("review", "d"), _skill("hidden", "h", include=False)])

    assert fingerprint_summaries(base) == fingerprint_summaries(with_cmd_only)


# =================================================================================================
# update rendering
# =================================================================================================


def test_an_update_names_what_arrived_and_what_left():
    before = summarize_skills([_skill("gone", "d")])
    after = summarize_skills([_skill("new", "d")])

    text = render_update(before, after)

    assert "Added: new" in text
    # Removal must be stated, not merely absent: the model is looking at an
    # older copy of the catalog higher up in the same conversation.
    assert "No longer available (do not call these): gone" in text
    assert "supersedes the previous available skills list" in text


def test_an_update_carries_the_whole_current_catalog():
    before = summarize_skills([])
    after = summarize_skills([_skill("a", "first"), _skill("b", "second")])

    text = render_update(before, after)

    assert "<name>a</name>" in text
    assert "<name>b</name>" in text


def test_an_update_renders_one_line_per_skill_not_one_per_character():
    """`lines.extend(rendered)` yields a bullet per character. Pinned because
    the same mistake shipped once in the tool-usage section."""
    after = summarize_skills([_skill("review", "A review skill.")])

    text = render_update(summarize_skills([]), after)

    assert text.count("<available_skills>") == 1
    # A per-character render puts every letter of every tag on its own line.
    # The catalog is one block of consecutive lines instead.
    block = text[text.index("<available_skills>") :]
    assert block.splitlines() == [
        "<available_skills>",
        "  <skill>",
        "    <name>review</name>",
        "    <description>A review skill.</description>",
        "  </skill>",
        "</available_skills>",
    ]


def test_a_changed_description_is_reported_as_an_update():
    before = summarize_skills([_skill("review", "old")])
    after = summarize_skills([_skill("review", "new")])

    assert "Updated descriptions: review" in render_update(before, after)


def test_an_unchanged_catalog_still_renders_when_asked():
    """render_update is only called on a real change; it must not crash if it is."""
    same = summarize_skills([_skill("review", "d")])

    assert "review" in render_update(same, same)


# =================================================================================================
# removal
# =================================================================================================


def test_removal_says_so_rather_than_rendering_an_empty_block():
    """An empty `<available_skills>` reads as "this harness has none"."""
    text = render_removed()

    assert "Skill guidance is no longer available" in text
    assert "No skills are currently available." in text
    assert "<available_skills>" not in text


def test_format_summaries_states_emptiness_explicitly():
    assert "No skills are currently available." in format_summaries(())


# =================================================================================================
# the message wrapper
# =================================================================================================


def test_the_refresh_message_is_marked_as_a_system_event():
    message = build_skills_refresh_message(render_removed())

    assert is_skills_refresh_message(message)
    assert SKILLS_REFRESH_TAG in message.content
    assert "not a user instruction" in message.content


def test_a_regular_message_is_not_a_refresh():
    from vtx.core.types import UserMessage

    assert not is_skills_refresh_message(UserMessage(content="hello"))


def test_escaping_survives_a_hostile_description():
    hostile = (SkillSummary(name="x", description="<script>&\"'</script>"),)

    rendered = format_summaries(hostile)

    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
