"""The swarm tools: the board, the channels, and the roster that talks on it.

``swarm``  workers, channels, models, title — the swarm itself
``board``  post (`@name` is how you hand out work), read after a sequence
           number, or park until a post names you (free, unlike polling)

The board's three verbs are one tool because they are one object: posting,
reading and parking are the same conversation, and three schemas for it was
three ways to say the same thing.

``board_tools(engine, name)`` and ``worker_swarm_tool(engine, name)`` build
bound copies for a worker; the registered copies act as the coordinator of the
current conversation and create the swarm on first use.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from ctx.agent.tools.base import BaseTool
from ctx.protocol.types import ToolResult
from pydantic import BaseModel, Field

from vx.swarm import mentions as mn
from vx.swarm.engine import SwarmError

Action = Literal["add", "status", "models", "model", "kill", "title", "channel"]
ChannelOp = Literal["create", "list", "subscribe", "unsubscribe", "archive"]


class SwarmParams(BaseModel):
    action: Action = Field(
        description=(
            "add (spawn a worker), status (who is waiting/working), models (what add accepts), "
            "model (switch a member's model), kill, title, channel (create/list/subscribe/"
            "unsubscribe/archive a work area)"
        )
    )
    name: str = Field(default="", description="Roster name for add/kill/model: short, lowercase")
    brief: str = Field(
        default="",
        description=(
            "For add: the worker's whole assignment. It starts fresh and sees nothing of "
            "this conversation, so say what it owns and what done looks like. Posted publicly."
        ),
    )
    model: str | None = Field(
        default=None, description="For add/model: the model to pin (defaults to yours)"
    )
    tasks: list[str] | None = Field(
        default=None, description="For add: per-worker slices to spawn <name>-1..k as a group"
    )
    n: int | None = Field(default=None, description="For add: number of homogeneous copies")
    title: str = Field(default="", description="For title: a short name for the swarm")
    role: str = Field(
        default="worker",
        description=(
            "For add: worker (default), verifier (reports, does not fix), or narrator/monitor "
            "(roles that see every channel)."
        ),
    )
    channels: list[str] | None = Field(
        default=None,
        description=(
            "For add: channels this worker joins besides #main. Its brief is posted in the "
            "first one and its posts go there by default."
        ),
    )
    workspace: Literal["inherit", "share", "branch"] = Field(
        default="inherit",
        description=(
            "For add: inherit (your working copy, default), share (its own git worktree of "
            "your repo, for authors that edit or build at the same time), branch (an isolated "
            "clone). kill deletes it, so land the branch first."
        ),
    )
    fork: str = Field(
        default="",
        description=(
            'For add: roster name of a member (or "coordinator") whose conversation history the '
            "new worker starts from instead of cold. Use it when the context is already warm."
        ),
    )
    all: bool = Field(
        default=False, description="For models: list every model, not just the first page"
    )
    channel: str = Field(default="", description="For channel: the channel name, e.g. parser")
    op: ChannelOp = Field(
        default="list",
        description="For channel: create | list | subscribe | unsubscribe | archive",
    )
    topic: str = Field(default="", description="For channel create: what this area owns")


class BoardParams(BaseModel):
    """The board's three verbs. Each one reads the fields it needs and ignores the rest."""

    action: Literal["post", "read", "wait"] = Field(
        description=(
            "`post` says something (needs `text`); `read` returns numbered posts (needs "
            "`since`); `wait` parks this turn until somebody posts (needs `since`)."
        )
    )
    text: str = Field(
        default="",
        description=(
            "For post. Name who should act on it: `@parser take the lexer tests`, `@all new "
            "rule: ...`, `@#ui is the header done`. A post with no @ wakes nobody."
        ),
    )
    since: int = Field(
        default=0,
        description=(
            "For read and wait: only posts after this sequence number. Pass the last one you "
            "actually read, not the board head, so nothing posted while you worked is slept "
            "through."
        ),
    )
    channel: str = Field(
        default="",
        description=(
            "For post and read. Post: empty is #main for the coordinator, the worker's own "
            "area for a worker. Read: one channel, `*` for every channel, empty for your own "
            "view (#main plus the channels you follow)."
        ),
    )
    limit: int = Field(default=50, description="For read: newest N posts after `since`")
    mentions_only: bool = Field(
        default=True,
        description=(
            "For wait: wake only for a post that names you, your channel, or @all (default). "
            "False wakes for anything in your channels — use it when you are free and hunting."
        ),
    )
    timeout: int = Field(
        default=0, description="For wait: give up after this many seconds (0 waits until woken)"
    )


class _SwarmTool(BaseTool):
    """Shared plumbing: a tool bound either to the coordinator or to a worker."""

    mutating = False
    parallel_safe = False
    tool_icon = "⬡"

    def __init__(self, engine: object | None = None, member: str = mn.COORDINATOR) -> None:
        self._engine = engine
        self._member = member

    def _get_engine(self):
        from vx.swarm.engine import current_engine

        return self._engine if self._engine is not None else current_engine()

    async def run(self, params: Any, cancel_event: asyncio.Event | None = None) -> ToolResult:
        try:
            return await self._run(params, cancel_event)
        except SwarmError as exc:
            return ToolResult(success=False, result=str(exc), ui_summary=f"swarm: {exc}"[:80])

    async def _run(self, params: Any, cancel_event: asyncio.Event | None) -> ToolResult:
        raise NotImplementedError


class SwarmTool(_SwarmTool):
    name = "swarm"
    params = SwarmParams
    tool_icon = "⬡"
    description = (
        "Run a swarm: several real agents working in parallel on one task, each in its own "
        "conversation, coordinating through a shared message board you own. Use it when the "
        "user asks for a swarm, several agents at once, or work that splits cleanly; use "
        "`delegate_subagent` for one isolated side task. Actions: add spawns a worker (pinning "
        "a model, workspace and channels) and posts its brief as `@name <brief>`; status lists "
        "who is waiting, working, stopped or killed; models lists what add accepts; model "
        "switches a member's model; kill ends a worker for good; title names the swarm; channel "
        "creates/ lists/ subscribes/ archives work areas so unrelated work stops waking other "
        "workers. With the swarm started, hand out work on the `board` (@name, @#channel, "
        '@all), read it, and park with `board{action:"wait"}` instead of polling.'
    )

    def format_call(self, params: SwarmParams) -> str:
        action = params.action
        if action == "add":
            where = f" #{params.channel}" if params.channel else ""
            if params.tasks:
                return f"add {params.name}{where} ×{len(params.tasks)}"
            if params.n and params.n > 1:
                return f"add {params.name}{where} ×{params.n}"
            return f"add @{params.name or 'worker'}{where}"
        if action == "kill":
            return f"kill @{params.name}"
        if action == "model":
            return f"model @{params.name} → {params.model}"
        if action == "title":
            return f'title "{params.title}"'
        if action == "channel":
            return f"#{params.channel or '?'} {params.op}"
        if action == "models":
            return "models"
        return "status"

    async def execute(
        self, params: SwarmParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        return await self.run(params, cancel_event)

    async def _run(self, params: SwarmParams, cancel_event: asyncio.Event | None) -> ToolResult:
        from vx.swarm.engine import SwarmError

        engine = self._get_engine()
        actor = self._member
        is_coordinator = actor == mn.COORDINATOR
        if params.action == "title" and not is_coordinator:
            raise SwarmError("Only the coordinator names the swarm.")
        if params.action == "title":
            title = engine.set_title(params.title)
            return ToolResult(
                success=True, result=f'Swarm is now "{title}".', ui_summary=f"title: {title}"
            )
        if params.action == "status":
            text = engine.status()
            return ToolResult(success=True, result=text, ui_summary=text.splitlines()[0])
        if params.action == "models":
            text = engine.models(params.all)
            return ToolResult(success=True, result=text, ui_summary="model catalog")
        if params.action == "model":
            target = params.name or actor
            message = engine.switch_model(target, params.model or "", actor=actor)
            return ToolResult(success=True, result=message, ui_summary=message[:70])
        if params.action == "channel":
            message = engine.channel_action(
                params.op, params.channel, params.topic, params.name, actor=actor
            )
            return ToolResult(success=True, result=message, ui_summary=message[:70])
        if params.action == "kill":
            if not params.name:
                return ToolResult(success=False, result="kill needs the member's name.")
            message = await engine.kill(params.name, actor=actor)
            return ToolResult(success=True, result=message, ui_summary=message)
        if not params.name:
            return ToolResult(
                success=False, result="add needs a name for the worker, e.g. name='parser'."
            )
        if not params.brief.strip():
            return ToolResult(
                success=False,
                result=(
                    "add needs a brief. The worker starts fresh: it has not seen this "
                    "conversation, so the brief must say what it owns and what done looks like."
                ),
            )
        names = await engine.add(
            params.name,
            params.brief,
            model=params.model,
            role=params.role,
            tasks=params.tasks,
            n=params.n,
            channels=params.channels,
            workspace=params.workspace,
            fork=params.fork,
            added_by=actor,
        )
        listed = ", ".join(f"@{n}" for n in names)
        return ToolResult(
            success=True,
            result=(
                f"Spawned {listed}. The brief is on the board; they are working now.\n"
                f"{engine.status()}"
            ),
            ui_summary=f"spawned {listed}",
        )


class BoardTool(_SwarmTool):
    """One tool for the board, because the board is one thing.

    Posting, reading and parking are the same conversation with a different verb;
    they travel together and were three schemas for one object. The verbs stay
    distinct inside, and ``wait`` keeps its own name in `format_call` so a turn
    that parks is never mistaken for a turn that reads.
    """

    name = "board"
    params = BoardParams
    tool_icon = "☰"
    description = (
        "The swarm board: one numbered, append-only conversation shared by you and every "
        "worker. `post` assigns work and answers workers (`@name` wakes that worker, `@#area` "
        "wakes a channel, `@all` wakes everyone); `read` returns posts after a sequence "
        "number; `wait` parks until somebody posts something that concerns you, which is free "
        "— polling `read` in a loop is not. Posts are kept: refer to earlier ones by `#seq` "
        "instead of repeating them."
    )

    def format_call(self, params: BoardParams) -> str:
        if params.action == "post":
            text = " ".join(params.text.split())
            where = f"#{params.channel} " if params.channel else ""
            return f"post {where}{text[:58]}" + ("…" if len(text) > 58 else "")
        if params.action == "wait":
            mode = "any post" if not params.mentions_only else "mentions"
            return f"wait since #{params.since} · {mode}"
        where = f"#{params.channel} " if params.channel else ""
        return f"read {where}since #{params.since}"

    async def execute(
        self, params: BoardParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        return await self.run(params, cancel_event)

    async def _run(self, params: BoardParams, cancel_event: asyncio.Event | None) -> ToolResult:
        if params.action == "post":
            return await self._post(params)
        if params.action == "wait":
            return await self._wait(params)
        return self._read(params)

    async def _post(self, params: BoardParams) -> ToolResult:
        if not params.text.strip():
            raise SwarmError(
                'board{action:"post"} needs text, e.g. text:"@parser take the lexer".'
            )
        engine = self._get_engine()
        post = await engine.post(self._member, params.text, params.channel or None)
        woken = [name for name in engine.woken_by(post) if name != self._member]
        note = f" · woke {', '.join('@' + n for n in woken)}" if woken else ""
        where = "" if post.channel == "main" else f" in #{post.channel}"
        result = f"Posted #{post.seq}{where}.{(' ' + note.strip(' ·')) if note else ''}"
        return ToolResult(success=True, result=result, ui_summary=f"#{post.seq}{note}")

    def _read(self, params: BoardParams) -> ToolResult:
        engine = self._get_engine()
        text = engine.read(
            self._member, params.since, max(1, min(params.limit, 200)), params.channel or None
        )
        return ToolResult(success=True, result=text, ui_summary=f"since #{params.since}")

    async def _wait(self, params: BoardParams) -> ToolResult:
        if params.since <= 0:
            raise SwarmError(
                'board{action:"wait"} needs since: the last sequence number you actually read '
                "(the last post you read), or it cannot tell what is new."
            )
        engine = self._get_engine()
        text = await engine.wait(
            self._member,
            since=params.since,
            mentions_only=params.mentions_only,
            timeout=max(0, params.timeout),
        )
        return ToolResult(success=True, result=text, ui_summary=f"since #{params.since}")


def board_tools(engine: object, member: str) -> list[BaseTool]:
    """The board bound to one worker: it posts and reads as that member."""
    return [BoardTool(engine, member)]


def worker_swarm_tool(engine: object, member: str) -> BaseTool:
    """A worker's own ``swarm`` tool: helpers, status, its own model and channels."""
    return SwarmTool(engine, member)


__all__ = [
    "BoardParams",
    "BoardTool",
    "SwarmParams",
    "SwarmTool",
    "board_tools",
    "worker_swarm_tool",
]
