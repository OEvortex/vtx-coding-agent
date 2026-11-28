"""The swarm: board, mentions, worker wake loop, and the tools on top."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from vx.swarm import engine as engine_mod
from vx.swarm.board import STOPPED, WAITING, Board, MemberRow
from vx.swarm.engine import SwarmEngine, SwarmError, close_all_engines, engine_for
from vx.swarm.mentions import addressed, group_of, mentions
from vx.tools.swarm import BoardParams, BoardTool, SwarmParams, SwarmTool

# -- mentions ----------------------------------------------------------


class TestMentions:
    def test_names_all_and_groups(self):
        assert mentions("@parser take this") == {"parser"}
        assert mentions("@all stand up") == {"all"}
        assert addressed("@all anything", "parser")
        assert addressed("@parser go", "parser")
        assert addressed("@pars go", "parser") is False

    def test_group_names_reach_numbered_copies(self):
        assert group_of("parser-2") == "parser"
        assert group_of("parser") == "parser"
        assert addressed("@parser go", "parser-2")
        assert addressed("@parser-2 go", "parser-2")
        assert addressed("@parser-2 go", "parser-3") is False

    def test_case_and_word_boundaries(self):
        assert addressed("@Parser go", "parser")
        assert mentions("mail me at me@parser.dev") == set()
        assert mentions("@#ui hello") == set()
        assert addressed("no mentions here", "parser") is False


# -- board -------------------------------------------------------------


class TestBoard:
    def test_append_and_read_since(self, tmp_path):
        board = Board(tmp_path)
        first = board.append("coordinator", "hello")
        second = board.append("parser", "@coordinator hi")
        assert (first.seq, second.seq) == (1, 2)
        assert [p.seq for p in board.since(1)] == [2]
        assert board.head == 2

    def test_reopening_reads_what_is_on_disk(self, tmp_path):
        Board(tmp_path).append("coordinator", "first")
        reopened = Board(tmp_path)
        assert reopened.head == 1

    def test_roster_round_trip(self, tmp_path):
        board = Board(tmp_path, title="parser")
        board.upsert(MemberRow(name="parser", model="m", state=WAITING, last_read_seq=3))
        assert board.members_path.exists()

        again = Board(tmp_path)
        again.load()
        assert again.title == "parser"
        row = again.rows["parser"]
        assert (row.model, row.state, row.last_read_seq) == ("m", WAITING, 3)


# -- engine ------------------------------------------------------------


class ScriptedAgent:
    """A stand-in worker: runs the steps its script holds, one per turn."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.prompts: list[str] = []

    async def run(self, prompt, cancel_event=None):
        self.prompts.append(prompt)
        if self.steps:
            await self.steps.pop(0)(self, prompt)
        for event in ():  # pragma: no cover - keeps the async-generator shape
            yield event


def make_engine(tmp_path):
    parent = SimpleNamespace(
        cwd=str(tmp_path),
        model="test-model",
        model_provider=None,
        base_url=None,
        thinking_level="high",
        provider=None,
        session=None,
    )
    return SwarmEngine(parent, root=tmp_path / "swarm")


def install(engine, scripts):
    """Replace provider/session bootstrap with scripted workers."""

    async def build(member, **kwargs):
        member.row.conversation_id = f"conv-{member.name}"
        member.agent = ScriptedAgent(scripts.get(member.name, []))

    engine._build_agent = build  # type: ignore[method-assign]


async def wait_until(predicate, timeout=3.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


class TestEngine:
    @pytest.mark.asyncio
    async def test_a_worker_wakes_on_its_mention(self, tmp_path):
        engine = make_engine(tmp_path)
        seen = {}

        async def work(agent, prompt):
            await engine.post("parser", "@coordinator started")
            cursor = engine.head
            text = await engine.wait("parser", since=cursor)
            seen["woke_with"] = text
            await engine.post("parser", "@coordinator [done] parsed")

        install(engine, {"parser": [work]})
        names = await engine.add("parser", "Own src/parser.rs")
        assert names == ["parser"]
        assert await wait_until(lambda: engine.members["parser"].row.last_read_seq > 0)

        await engine.post("coordinator", "@parser the tests are red, go")
        assert await wait_until(lambda: "woke_with" in seen)
        assert "the tests are red" in seen["woke_with"]

        await wait_until(lambda: engine.members["parser"].state == STOPPED)
        texts = [p.text for p in engine.board.since(0)]
        assert any("[done]" in t for t in texts)
        # The brief was posted as an @mention, so the board shows the assignment.
        assert texts[0].startswith("@parser")
        await engine.close()

    @pytest.mark.asyncio
    async def test_group_add_creates_numbered_copies(self, tmp_path):
        engine = make_engine(tmp_path)
        install(engine, {})
        names = await engine.add("parser", "Own one section", tasks=["lexer", "types", "errors"])
        assert names == ["parser-1", "parser-2", "parser-3"]
        text = engine.status()
        assert "@parser-1" in text and "@parser-3" in text
        assert addressed("@parser go", "parser-2")
        await engine.close()

    @pytest.mark.asyncio
    async def test_any_post_mode_wakes_on_anything(self, tmp_path):
        engine = make_engine(tmp_path)
        got = {}

        async def work(agent, prompt):
            got["posts"] = await engine.wait("verifier", since=engine.head, mentions_only=False)

        install(engine, {"verifier": [work]})
        await engine.add("verifier", "Check the diff")
        await wait_until(lambda: engine.members["verifier"].state == WAITING)
        await engine.post("coordinator", "no name in this one")
        assert await wait_until(lambda: "posts" in got)
        assert "no name in this one" in got["posts"]
        await engine.close()

    @pytest.mark.asyncio
    async def test_waiting_alone_does_not_hang(self, tmp_path):
        engine = make_engine(tmp_path)
        note = await engine.wait("coordinator", since=0)
        assert "Nobody else is running" in note

    @pytest.mark.asyncio
    async def test_wait_times_out(self, tmp_path):
        engine = make_engine(tmp_path)
        install(engine, {})
        member = await engine._start(
            engine_mod.SpawnRequest(name="idle", role="worker", brief="b", model="m")
        )
        member.model = "m"
        await wait_until(lambda: True)
        note = await engine.wait("idle", since=0, timeout=0.05)
        assert "nothing woke you" in note
        await engine.close()

    @pytest.mark.asyncio
    async def test_kill_is_final_and_replacing_takes_a_new_name(self, tmp_path):
        engine = make_engine(tmp_path)
        install(engine, {})
        await engine.add("parser", "Own it")
        message = await engine.kill("parser")
        assert "cannot be revived" in message
        assert engine.members["parser"].row.killed
        names = await engine.add("parser", "Own it again")
        assert names == ["parser-2"]
        await engine.close()

    @pytest.mark.asyncio
    async def test_errors_are_reported_not_raised(self, tmp_path):
        engine = make_engine(tmp_path)
        install(engine, {})
        with pytest.raises(SwarmError):
            await engine.add("parser", "")
        with pytest.raises(SwarmError):
            await engine.add("all", "brief")
        await engine.close()

    @pytest.mark.asyncio
    async def test_title_and_dir(self, tmp_path):
        engine = make_engine(tmp_path)
        assert engine.set_title("the parser rewrite") == "the parser rewrite"
        assert engine.dir.exists()
        assert "the parser rewrite" in engine.status()
        await engine.close()

    @pytest.mark.asyncio
    async def test_worker_bound_tools_share_the_coordinator_board(self, tmp_path):
        """A worker posting through its own tool instances lands on the same board."""
        from vx.tools.swarm import board_tools

        engine = make_engine(tmp_path)
        result = {}

        async def work(agent, prompt):
            (board,) = board_tools(engine, "parser")
            await board.execute(BoardParams(action="post", text="@coordinator lexer start"))
            seen = await board.execute(BoardParams(action="wait", since=engine.head))
            result["woke"] = seen.result
            await board.execute(BoardParams(action="post", text="@coordinator [done] lexer works"))

        install(engine, {"parser": [work]})
        await engine.add("parser", "Own the lexer")
        assert await wait_until(lambda: engine.members["parser"].state == WAITING)

        await BoardTool(engine, "coordinator").execute(
            BoardParams(action="post", text="@parser hand me the token list")
        )
        assert await wait_until(lambda: "woke" in result)
        assert "token list" in (result["woke"] or "")

        await wait_until(lambda: any("[done]" in p.text for p in engine.board.since(0)))
        posts = [(p.agent, p.text) for p in engine.board.since(0)]
        assert ("parser", "@coordinator lexer start") in posts
        await engine.close()


# -- channels ----------------------------------------------------------


class TestChannels:
    @pytest.mark.asyncio
    async def test_create_list_and_post_in_a_channel(self, tmp_path):
        engine = make_engine(tmp_path)
        install(engine, {})
        tool = SwarmTool(engine)
        made = await tool.execute(
            SwarmParams(action="channel", op="create", channel="parser", topic="lexer")
        )
        assert made.success and "#parser" in (made.result or "")
        listed = await tool.execute(SwarmParams(action="channel", op="list"))
        assert "#parser" in (listed.result or "") and "lexer" in (listed.result or "")
        again = await tool.execute(SwarmParams(action="channel", op="create", channel="parser"))
        assert again.success is False
        await engine.close()

    @pytest.mark.asyncio
    async def test_a_worker_only_sees_its_own_channels(self, tmp_path):
        engine = make_engine(tmp_path)
        install(engine, {})
        engine.channel_action("create", "parser", "lexer")
        engine.channel_action("create", "ui", "renderer")
        await engine.add("parser", "Own the lexer", channels=["parser"])
        await engine.add("ui", "Own the renderer", channels=["ui"])

        await engine.post("coordinator", "@parser lexer detail", "parser")
        await engine.post("coordinator", "@ui renderer detail", "ui")

        parser_view = engine.read("parser", since=0)
        assert "lexer detail" in parser_view and "renderer detail" not in parser_view
        everything = engine.read("parser", since=0, channel="*")
        assert "lexer detail" in everything and "renderer detail" in everything
        # The coordinator sees every channel by default.
        assert "renderer detail" in engine.read("coordinator", since=0)
        await engine.close()

    @pytest.mark.asyncio
    async def test_channel_call_wakes_the_area(self, tmp_path):
        engine = make_engine(tmp_path)
        got = {}

        async def work(agent, prompt):
            got["posts"] = await engine.wait("parser", since=engine.head)

        install(engine, {"parser": [work]})
        engine.channel_action("create", "parser", "lexer")
        await engine.add("parser", "Own the lexer", channels=["parser"])
        await wait_until(lambda: engine.members["parser"].state == WAITING)

        await engine.post("coordinator", "@#parser is the AST stable?")
        assert await wait_until(lambda: "posts" in got)
        assert "AST stable" in got["posts"]
        await engine.close()

    @pytest.mark.asyncio
    async def test_archive_stops_posts_and_main_call_is_refused(self, tmp_path):
        engine = make_engine(tmp_path)
        engine.channel_action("create", "parser", "lexer")
        engine.channel_action("archive", "parser")
        with pytest.raises(SwarmError):
            await engine.post("coordinator", "late", "parser")
        with pytest.raises(SwarmError):
            await engine.post("coordinator", "@#main everybody")
        # The channel and its history stay readable.
        assert "#parser" in engine.read("coordinator", since=0, channel="parser")
        assert "[archived]" in engine.channel_action("list")
        await engine.close()

    @pytest.mark.asyncio
    async def test_subscribe_moves_a_worker_and_only_its_own(self, tmp_path):
        engine = make_engine(tmp_path)
        install(engine, {})
        engine.channel_action("create", "ui", "renderer")
        await engine.add("ui", "Own the renderer")
        moved = engine.channel_action("subscribe", "ui", name="ui", actor="ui")
        assert "#ui" in moved
        with pytest.raises(SwarmError):
            engine.channel_action("subscribe", "ui", name="ui", actor="parser")
        await engine.close()


# -- workspaces, forks, models, helpers --------------------------------


class TestGrownUpSwarm:
    @pytest.mark.asyncio
    async def test_share_workspace_is_a_worktree_that_kill_removes(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "readme.md").write_text("hello\n")
        for args in (("init", "-q"), ("add", "."), ("commit", "-qm", "init")):
            code, out = await engine_mod._git(*args, cwd=str(repo))
            assert code == 0, out

        engine = make_engine(repo)
        install(engine, {})
        await engine.add("parser", "Own the lexer", workspace="share")
        await wait_until(lambda: engine.members["parser"].task is not None)
        member = engine.members["parser"]

        assert member.row.workspace == "share"
        assert member.cwd and member.cwd != str(repo)
        assert Path(member.cwd).is_dir()
        assert "workspace:share" in engine.status()

        await engine.kill("parser")
        assert not Path(member.cwd).exists()
        await engine.close()

    @pytest.mark.asyncio
    async def test_workspace_outside_a_repo_is_refused(self, tmp_path):
        engine = make_engine(tmp_path)  # tmp_path is not a git repository
        with pytest.raises(SwarmError, match="git repository"):
            await engine.add("parser", "Own the lexer", workspace="share")
        assert "parser" not in engine.members
        await engine.close()

    @pytest.mark.asyncio
    async def test_fork_copies_the_words_of_the_source(self, tmp_path):
        from ctx.protocol.types import AssistantMessage, TextContent, UserMessage

        engine = make_engine(tmp_path)

        class Source:
            def all_messages(self):
                return [
                    UserMessage(content="we picked sqlite for the store"),
                    AssistantMessage(content=[TextContent(text="agreed, WAL mode")]),
                ]

        class Target:
            def __init__(self):
                self.messages = []

            def append_message(self, message):
                self.messages.append(message)

        engine.members["planner"] = engine_mod.Member(
            name="planner", role="worker", row=MemberRow(name="planner"), session=Source()
        )
        member = engine_mod.Member(name="writer", role="worker", fork_from="planner")
        target = Target()
        engine._fork_history(member, target)
        texts = [
            m.content if isinstance(m.content, str) else "".join(p.text for p in m.content)
            for m in target.messages
        ]
        assert texts == ["we picked sqlite for the store", "agreed, WAL mode"]
        await engine.close()

    @pytest.mark.asyncio
    async def test_model_switch_lands_at_the_next_turn(self, tmp_path):
        engine = make_engine(tmp_path)
        install(engine, {})
        await engine.add("parser", "Own it")
        message = engine.switch_model("parser", "other-model")
        assert "other-model" in message
        assert engine.members["parser"].pending_model == "other-model"
        with pytest.raises(SwarmError):
            engine.switch_model("coordinator", "m")
        with pytest.raises(SwarmError):
            engine.switch_model("parser", "other-model", actor="stranger")
        await engine.close()

    @pytest.mark.asyncio
    async def test_worker_helpers_and_ownership(self, tmp_path):
        engine = make_engine(tmp_path)
        install(engine, {})
        await engine.add("parser", "Own the lexer")
        helpers = await engine.add("sub", "Check the tokens", added_by="parser")
        assert helpers == ["sub"]
        assert "added by @parser" in engine.status()

        with pytest.raises(SwarmError):
            await engine.kill("sub", actor="ui-neighbour")
        message = await engine.kill("parser")
        assert "helpers survive" in message and "@sub" in message
        await engine.kill("sub")
        await engine.close()

    @pytest.mark.asyncio
    async def test_narrator_role_sees_every_channel(self, tmp_path):
        engine = make_engine(tmp_path)
        install(engine, {})
        engine.channel_action("create", "ui", "renderer")
        await engine.add("narrator", "Tell the story", role="narrator")
        await engine.post("coordinator", "@ui private note", "ui")
        assert "private note" in engine.read("narrator", since=0)
        await engine.close()

    @pytest.mark.asyncio
    async def test_close_all_engines_closes_and_forgets_them(self, tmp_path):
        parent = SimpleNamespace(session=SimpleNamespace(id="s1"), cwd=str(tmp_path))
        engine = engine_for(parent)
        install(engine, {})
        await engine.add("parser", "Own it")
        member = engine.members["parser"]

        await close_all_engines()

        assert engine._closed
        assert member.row.killed
        assert member.task is not None and member.task.done()
        # The registry is swept, so a fresh engine comes back.
        fresh = engine_for(parent)
        assert fresh is not engine
        await close_all_engines()
        await close_all_engines()  # idempotent: nothing left to close

    @pytest.mark.asyncio
    async def test_close_all_engines_sweeps_even_if_one_raises(self, tmp_path):
        parent_a = SimpleNamespace(session=SimpleNamespace(id="a"), cwd=str(tmp_path))
        parent_b = SimpleNamespace(session=SimpleNamespace(id="b"), cwd=str(tmp_path))
        bad = engine_for(parent_a)
        good = engine_for(parent_b)

        async def boom():
            raise RuntimeError("close failed")

        bad.close = boom  # type: ignore[method-assign]

        await close_all_engines()

        assert good._closed
        assert engine_for(parent_a) is not bad
        assert engine_for(parent_b) is not good


# -- tools -------------------------------------------------------------


class TestTools:
    @pytest.mark.asyncio
    async def test_swarm_add_status_kill(self, tmp_path):
        engine = make_engine(tmp_path)
        install(engine, {})
        tool = SwarmTool(engine)
        result = await tool.execute(SwarmParams(action="add", name="docs", brief="Write it"))
        assert result.success and "@docs" in (result.result or "")
        status = await tool.execute(SwarmParams(action="status"))
        assert "@docs" in (status.result or "")
        killed = await tool.execute(SwarmParams(action="kill", name="docs"))
        assert killed.success
        missing = await tool.execute(SwarmParams(action="kill", name="ghost"))
        assert missing.success is False
        await engine.close()

    @pytest.mark.asyncio
    async def test_add_without_brief_is_refused(self, tmp_path):
        engine = make_engine(tmp_path)
        result = await SwarmTool(engine).execute(SwarmParams(action="add", name="docs"))
        assert result.success is False
        assert "brief" in (result.result or "")
        await engine.close()

    @pytest.mark.asyncio
    async def test_the_board_tool_posts_reads_and_waits(self, tmp_path):
        engine = make_engine(tmp_path)
        install(engine, {"parser": []})
        await engine.add("parser", "Own it")
        as_coordinator = BoardTool(engine, "coordinator")
        as_parser = BoardTool(engine, "parser")

        sent = await as_coordinator.execute(BoardParams(action="post", text="@parser do it"))
        assert sent.success and "#2" in (sent.result or "")

        # The post names parser, so waiting returns it rather than parking.
        parked = await as_parser.execute(BoardParams(action="wait", since=1))
        assert "do it" in (parked.result or "")

        read_back = await as_parser.execute(BoardParams(action="read", since=0))
        assert "do it" in (read_back.result or "")
        await engine.close()

    @pytest.mark.asyncio
    async def test_the_board_tool_says_what_it_is_missing(self, tmp_path):
        engine = make_engine(tmp_path)
        install(engine, {"parser": []})
        await engine.add("parser", "Own it")
        board = BoardTool(engine, "coordinator")

        empty = await board.execute(BoardParams(action="post", text="  "))
        assert empty.success is False
        assert "needs text" in (empty.result or "")

        # Waiting needs to know what "new" means, or it would return history.
        blind = await board.execute(BoardParams(action="wait"))
        assert blind.success is False
        assert "since" in (blind.result or "")
        await engine.close()

    def test_the_board_tool_calls_itself_by_its_verb(self):
        board = BoardTool()
        assert (
            board.format_call(BoardParams(action="post", text="@parser go")) == "post @parser go"
        )
        assert board.format_call(BoardParams(action="wait", since=7)) == "wait since #7 · mentions"
        assert board.format_call(BoardParams(action="read", since=7)) == "read since #7"


# -- worker permissions -----------------------------------------------------


class Approving:
    """An agent that asks for permission, then ends its turn.

    A fresh future per turn, because that is what the harness does: each
    ``ToolApprovalEvent`` is answered exactly once and never reused.
    """

    def __init__(self) -> None:
        self.future: asyncio.Future | None = None
        self.turns = 0

    async def run(self, prompt, cancel_event=None):
        from ctx.core import ToolApprovalEvent

        self.turns += 1
        self.future = asyncio.get_running_loop().create_future()
        yield ToolApprovalEvent(
            tool_call_id=f"call_{self.turns}",
            tool_name="bash",
            display="rm -rf build",
            future=self.future,
        )


async def approving_member(tmp_path):
    """A real engine with one member whose agent asks to run a command."""
    from vx.swarm.engine import SwarmEngine

    engine = SwarmEngine(_parent(tmp_path), root=tmp_path / "swarm")
    agent = Approving()

    async def build(member, **kwargs):
        member.agent = agent

    engine._build_agent = build  # type: ignore[method-assign]
    await engine.add("parser", "clean the build dir")
    member = engine.members["parser"]
    for _ in range(100):  # the member's own brief runs first; let it park
        if member.state == "stopped":
            break
        await asyncio.sleep(0.01)
    return engine, agent


@pytest.mark.asyncio
async def test_a_worker_approves_itself_when_nothing_is_watching(tmp_path):
    """Unattended is still the default: no hook, no waiting on a human."""
    from ctx.core import ApprovalResponse

    engine, agent = await approving_member(tmp_path)
    await engine._run_turn(engine.members["parser"], "go")
    assert agent.future is not None
    assert agent.future.result() is ApprovalResponse.APPROVE
    await engine.close()


@pytest.mark.asyncio
async def test_a_watching_ui_decides_and_the_board_says_it_is_waiting(tmp_path):
    from ctx.core import ApprovalResponse

    engine, agent = await approving_member(tmp_path)
    seen: list[tuple[str, str]] = []

    async def hook(member, event):
        seen.append((member.name, member.awaiting))
        return ApprovalResponse.DENY

    engine.approval_hook = hook
    member = engine.members["parser"]
    await engine._run_turn(member, "go")
    assert seen == [("parser", "bash rm -rf build")]
    assert agent.future is not None
    assert agent.future.result() is ApprovalResponse.DENY
    assert member.awaiting == ""  # nothing stays pending after the answer
    await engine.close()


@pytest.mark.asyncio
async def test_a_broken_hook_does_not_strand_a_worker(tmp_path):
    from ctx.core import ApprovalResponse

    engine, agent = await approving_member(tmp_path)

    async def hook(member, event):
        raise RuntimeError("the UI went away")

    engine.approval_hook = hook
    await engine._run_turn(engine.members["parser"], "go")
    assert agent.future is not None
    assert agent.future.result() is ApprovalResponse.APPROVE
    await engine.close()


@pytest.mark.asyncio
async def test_the_board_draws_a_member_waiting_on_you(tmp_path):
    engine, agent = await approving_member(tmp_path)
    member = engine.members["parser"]

    async def hook(live, event):
        view = snapshot_from_engine(engine)
        assert view.member("parser").state == "blocked"
        assert "needs you" in view.member("parser").activity
        assert "bash rm -rf build" in view.member("parser").activity
        return None  # fall through to the default

    engine.approval_hook = hook
    await engine._run_turn(member, "go")
    assert agent.future is not None and agent.future.done()
    await engine.close()


def _parent(tmp_path):
    from types import SimpleNamespace

    return SimpleNamespace(
        cwd=str(tmp_path),
        model="test-model",
        model_provider=None,
        base_url=None,
        thinking_level="high",
        provider=None,
        session=None,
    )


def snapshot_from_engine(engine):
    from vx.tui.swarm import snapshot_from_engine as take

    return take(engine)
