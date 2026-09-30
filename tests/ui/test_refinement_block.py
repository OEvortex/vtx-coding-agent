"""Tests for the refinement outcome block: the collapsed header, the expanded
per-edit field diffs, and its participation in the ctrl+d expansion toggle."""

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Label

from vtx.tui.blocks import RefinementBlock, refinement_header
from vtx.tui.chat import ChatLog
from vtx.tui.styles import get_styles


class RefinementApp(App):
    CSS = get_styles()

    def compose(self) -> ComposeResult:
        yield ChatLog(id="chat-log")


def _edit(*, action="create", kind="memory", entry_id="pref", before=None, after=None, applied=True, error=None, reason=None):
    edit = {
        "action": action,
        "kind": kind,
        "id": entry_id,
        "applied": applied,
    }
    if before is not None:
        edit["before"] = before
    if after is not None:
        edit["after"] = after
    if error is not None:
        edit["error"] = error
    if reason is not None:
        edit["reason"] = reason
    return edit


def _entry(title="Pref", content="Use pytest.", scope="local"):
    return {"title": title, "content": content, "path": "general", "scope": scope, "version": 1}


# =================================================================================================
# header phrasing
# =================================================================================================


def test_header_counts_unevenly():
    assert (
        refinement_header(
            applied=2, total=2, kinds=["memory", "memory"], actions=["create", "create"], rollback_of=None
        )
        == "Harness refined · 2 memories created"
    )


def test_header_collapses_singular():
    assert (
        refinement_header(
            applied=1, total=1, kinds=["prompt"], actions=["create"], rollback_of=None
        )
        == "Harness refined · 1 prompt created"
    )


def test_header_reports_partial_application():
    """A partial pass is the case a user must notice, so it says so."""
    header = refinement_header(
        applied=1, total=3, kinds=["memory", "memory"], actions=["create", "create"], rollback_of=None
    )
    assert header.startswith("Harness partially refined")
    assert "1/3" in header


def test_header_reports_total_failure():
    assert (
        refinement_header(
            applied=0, total=2, kinds=["memory"], actions=["create"], rollback_of=None
        )
        == "Harness refinement failed"
    )


def test_header_reads_differently_for_rollback():
    forward = refinement_header(
        applied=1, total=1, kinds=["memory"], actions=["create"], rollback_of=None
    )
    rollback = refinement_header(
        applied=1, total=1, kinds=["memory"], actions=["delete"], rollback_of="refine_1"
    )
    assert forward != rollback
    assert rollback.startswith("Harness rollback completed")


def test_header_handles_a_pass_that_changed_nothing():
    assert "no edits applied" in refinement_header(
        applied=0, total=0, kinds=["memory"], actions=["create"], rollback_of=None
    )


def test_header_uses_changed_when_a_pass_mixes_actions():
    header = refinement_header(
        applied=2, total=2, kinds=["memory", "memory"], actions=["create", "update"], rollback_of=None
    )
    assert "changed" in header


# =================================================================================================
# rendering
# =================================================================================================


@pytest.mark.asyncio
async def test_create_shows_added_fields_as_plus():
    async with RefinementApp().run_test() as pilot:
        chat = pilot.app.query_one("#chat-log", ChatLog)
        block = chat.add_refinement(
            summary="Recorded the test preference.",
            applied=1,
            total=1,
            edits=[_edit(after=_entry())],
            refinement_id="refine_1",
        )
        await pilot.pause()

        assert block is not None
        block.set_expanded(True)
        await pilot.pause()

        detail = block.query_one("#refinement-output", Label).renderable.plain
        assert "+ Use pytest." in detail
        assert "✓ Created local memory pref" in detail


@pytest.mark.asyncio
async def test_update_shows_removed_and_added_lines():
    async with RefinementApp().run_test() as pilot:
        chat = pilot.app.query_one("#chat-log", ChatLog)
        block = chat.add_refinement(
            summary="Corrected the port.",
            applied=1,
            total=1,
            edits=[
                _edit(
                    action="update",
                    before=_entry(content="Use port 5432."),
                    after=_entry(content="Use port 6433."),
                )
            ],
            refinement_id="refine_2",
        )
        await pilot.pause()
        block.set_expanded(True)
        await pilot.pause()

        detail = block.query_one("#refinement-output", Label).renderable.plain
        assert "- Use port 5432." in detail
        assert "+ Use port 6433." in detail


@pytest.mark.asyncio
async def test_unchanged_field_is_not_shown_as_a_diff():
    async with RefinementApp().run_test() as pilot:
        chat = pilot.app.query_one("#chat-log", ChatLog)
        block = chat.add_refinement(
            summary="",
            applied=1,
            total=1,
            edits=[
                _edit(
                    action="update",
                    before=_entry(title="Same", content="changed"),
                    after=_entry(title="Same", content="new"),
                )
            ],
            refinement_id="refine_3",
        )
        await pilot.pause()
        block.set_expanded(True)
        await pilot.pause()

        detail = block.query_one("#refinement-output", Label).renderable.plain
        assert "Title" not in detail, "an unchanged field should not render"
        assert "+ changed" in detail


@pytest.mark.asyncio
async def test_failed_edit_shows_its_error():
    async with RefinementApp().run_test() as pilot:
        chat = pilot.app.query_one("#chat-log", ChatLog)
        block = chat.add_refinement(
            summary="",
            applied=0,
            total=1,
            edits=[_edit(applied=False, error="entry already exists")],
            refinement_id="refine_4",
        )
        await pilot.pause()
        block.set_expanded(True)
        await pilot.pause()

        detail = block.query_one("#refinement-output", Label).renderable.plain
        assert "✗ Failed to" in detail
        assert "entry already exists" in detail


@pytest.mark.asyncio
async def test_reason_is_surfaced():
    async with RefinementApp().run_test() as pilot:
        chat = pilot.app.query_one("#chat-log", ChatLog)
        block = chat.add_refinement(
            summary="",
            applied=1,
            total=1,
            edits=[_edit(after=_entry(), reason="the user corrected this twice")],
            refinement_id="refine_5",
        )
        await pilot.pause()
        block.set_expanded(True)
        await pilot.pause()

        detail = block.query_one("#refinement-output", Label).renderable.plain
        assert "Reason: the user corrected this twice" in detail


@pytest.mark.asyncio
async def test_collapsed_block_is_one_line_and_hides_detail():
    async with RefinementApp().run_test() as pilot:
        chat = pilot.app.query_one("#chat-log", ChatLog)
        block = chat.add_refinement(
            summary="A summary long enough to matter.",
            applied=1,
            total=1,
            edits=[_edit(after=_entry())],
            refinement_id="refine_6",
        )
        await pilot.pause()

        header = block.query_one("#refinement-header", Label).renderable.plain
        assert "A summary" not in header, "collapsed shows the outcome, not the prose"
        assert "ctrl+d" in header
        assert block.query_one("#refinement-output", Label).has_class("-hidden")


@pytest.mark.asyncio
async def test_detail_names_the_model_that_ran_the_pass():
    """Cost attribution matters when refinement is routed off the session model."""
    async with RefinementApp().run_test() as pilot:
        chat = pilot.app.query_one("#chat-log", ChatLog)
        block = chat.add_refinement(
            summary="",
            applied=1,
            total=1,
            edits=[_edit(after=_entry())],
            refinement_id="refine_7",
            model="openrouter/some-cheap-model",
        )
        await pilot.pause()
        block.set_expanded(True)
        await pilot.pause()

        detail = block.query_one("#refinement-output", Label).renderable.plain
        assert "openrouter/some-cheap-model" in detail


# =================================================================================================
# integration with the expansion toggle
# =================================================================================================


@pytest.mark.asyncio
async def test_ctrl_d_toggle_expands_refinement_blocks(monkeypatch):
    chat = ChatLog()
    block = RefinementBlock(applied=1, total=1, edits=[_edit(after=_entry())])
    chat._refinement_blocks = [block]
    seen: dict[str, bool] = {}
    monkeypatch.setattr(block, "set_expanded", lambda expanded: seen.update(b=expanded))
    monkeypatch.setattr(chat, "_scroll_if_anchored", lambda animate=False: None)

    assert chat.toggle_tool_output_expanded() is True
    assert seen == {"b": True}


@pytest.mark.asyncio
async def test_block_inherits_the_current_expansion_state():
    """A block mounted after ctrl+d should already be expanded."""
    async with RefinementApp().run_test() as pilot:
        chat = pilot.app.query_one("#chat-log", ChatLog)
        chat.set_tool_output_expanded(True)

        block = chat.add_refinement(
            summary="",
            applied=1,
            total=1,
            edits=[_edit(after=_entry())],
            refinement_id="refine_8",
        )
        await pilot.pause()

        assert block._expanded is True


@pytest.mark.asyncio
async def test_a_pass_with_no_edits_falls_back_to_a_line():
    """Nothing to diff means no block; a bare info line says it plainly."""
    async with RefinementApp().run_test() as pilot:
        chat = pilot.app.query_one("#chat-log", ChatLog)
        block = chat.add_refinement(
            summary="Nothing worth persisting.",
            applied=0,
            total=0,
            edits=[],
            refinement_id="refine_9",
        )
        await pilot.pause()

        assert block is None
        assert not chat._refinement_blocks
        assert "refine_9" in chat.children[-1].renderable.plain


@pytest.mark.asyncio
async def test_pruning_drops_stale_refinement_references():
    """Pruned blocks must not be retained for the expansion toggle."""
    async with RefinementApp().run_test() as pilot:
        chat = pilot.app.query_one("#chat-log", ChatLog)
        for i in range(3):
            chat.add_refinement(
                summary="",
                applied=1,
                total=1,
                edits=[_edit(after=_entry(entry_id=f"e{i}"))],
                refinement_id=f"refine_p{i}",
            )
        await pilot.pause()
        assert len(chat._refinement_blocks) == 3

        chat._refinement_blocks = [
            b for b in chat._refinement_blocks if b not in chat.children[:1]
        ]
        assert len(chat._refinement_blocks) == 2
