"""Dynamic, context-aware witty status lines for Ctx TUI.

Provides witty status lines based on:
- Model & agent lifecycle states (thinking, reasoning, compacting, approval, etc.)
- Specific tool being executed (at least 10 lines per tool)
- Tool errors and failure conditions (at least 10 lines per tool)
"""

from __future__ import annotations

import random

# ---------------------------------------------------------------------------
# Agent / Model State Status Lines (10+ lines each)
# ---------------------------------------------------------------------------

AGENT_STATUS_LINES: dict[str, tuple[str, ...]] = {
    "thinking": (
        "Trying to parse what this codebase is doing...",
        "Wondering if this file has a purpose beyond existing...",
        "Deciphering a variable named 'data2'...",
        "Staring at a 400-line function like it owes me money...",
        "Reading a comment that just says 'fix later'...",
        "Contemplating why there are 17 configuration files...",
        "Wondering if this method was copy-pasted from Stack Overflow...",
        "Processing code written by 'future me' who hates me...",
        "Tracing a function through 6 different files...",
        "Figuring out if this is dead code or a ticking bomb...",
        "Unraveling nested ternary hell written at 2am...",
        "Questioning the existence of a magic number: 0.42...",
    ),
    "reasoning": (
        "Tracing a bug through a 'temporary' 3-year-old hack...",
        "Evaluating why someone added a try/except pass here...",
        "Weighing whether to refactor or add another workaround...",
        "Connecting dots between an API change and a broken test...",
        "Trying to understand why this works at all...",
        "Analyzing why the dev who wrote this left no comments...",
        "Figuring out if this race condition is intentional...",
        "Wondering if this lint rule was disabled on purpose...",
        "Tracing back a deleted function that something still imports...",
        "Debating whether this is tech debt or a feature...",
        "Following a thread through 4 different 'utils' modules...",
        "Reconstructing why this dependency was pinned to an old version...",
    ),
    "compacting": (
        "Packing the context window so you don't hit the limit again...",
        "Shrinking this chat so you can forget it faster...",
        "Compressing your life's work into a vague summary...",
        "Condensing 200 messages into 'he fixed a bug, then broke 3 more'...",
        "Summarizing this debugging session you'll definitely need tomorrow...",
        "Deleting context because you can't stop talking...",
        "Forgetting the things you said so you can lie about them later...",
        "Burning conversational history like it's sensitive data...",
        "Making room for more of your brilliant ideas...",
        "Packing up the evidence of this disaster...",
    ),
    "approval": (
        "Asking nicely before doing what you clearly want...",
        "Awaiting permission to clean up your mess...",
        "Waiting for you to review what you asked me to do...",
        "Pausing so you can panic at the diff...",
        "Standing by while you second-guess this...",
        "Letting you approve the refactor you begged for...",
        "Holding your destructive change hostage...",
        "Asking for a signature before breaking everything...",
        "Giving you one last chance to stop this...",
        "Pending your 'yes, I really want this' confirmation...",
    ),
    "general": (
        "Untangling the spaghetti you call architecture...",
        "Polishing the turd you left in main...",
        "Staring at your git history in horror...",
        "Wondering if this project has a roadmap or just vibes...",
        "Fixing the code you said was 'fine'...",
        "Figuring out which module doesn't need to exist...",
        "Counting the TODOs you'll never address...",
        "Reading your commit messages like a mystery novel...",
        "Wondering if tests exist or are just a myth here...",
        "Pretending this monolith is intentional...",
        "Searching for the logic in this illogical codebase...",
        "Preparing to judge your naming conventions...",
    ),
}

# ---------------------------------------------------------------------------
# Per-Tool Status Lines (10+ lines each)
# ---------------------------------------------------------------------------

DEFAULT_TOOL_STATUS_LINES: tuple[str, ...] = (
    "Executing whatever you asked for...",
    "Running the thing you'll ask me to do again...",
    "Invoking the tool you'll misuse later...",
    "Engaging the subsystem you definitely broke...",
    "Passing arguments you should have validated...",
    "Processing the request you barely described...",
    "Calling the capability you said was 'easy'...",
    "Dispatching the operation you'll forget about...",
    "Interfacing with the module you imported blindly...",
    "Working on the problem you created...",
)

RECAP_STATUS_LINES: tuple[str, ...] = (
    "Piecing together what you've been up to...",
    "Connecting the dots across this session...",
    "Replaying the highlights of this conversation...",
    "Figuring out what to say so you don't have to scroll...",
    "Summarizing the chaos you've created...",
    "Distilling this session into a sensible takeaway...",
    "Rewinding through the last few tool calls...",
    "Catching you up before you dive back in...",
    "Scanning the trail of breadcrumbs you left...",
    "Turning this session into a sensible summary...",
)

# ---------------------------------------------------------------------------
# Per-Tool Error Lines (10+ lines each)
# ---------------------------------------------------------------------------

DEFAULT_TOOL_ERROR_LINES: tuple[str, ...] = (
    "Went sideways because you didn't specify the tool...",
    "Wild exception because your input was garbage...",
    "Error status because you asked for something impossible...",
    "Unexpected failure because your environment is broken...",
    "Tripped on bad input because you didn't validate it...",
    "Interface errored because you're on the wrong version...",
    "Crashed because your prompt hit the token limit...",
    "Unexpected error because your config is outdated...",
    "Rejected by system because you don't have permission...",
    "Aborted because you interrupted it mid-flight...",
)

# ---------------------------------------------------------------------------
# Flat list of all status lines for backward compatibility
# ---------------------------------------------------------------------------

WITTY_STATUS_LINES: tuple[str, ...] = tuple(
    line
    for pool in (
        *AGENT_STATUS_LINES.values(),
        RECAP_STATUS_LINES,
        DEFAULT_TOOL_STATUS_LINES,
        DEFAULT_TOOL_ERROR_LINES,
    )
    for line in pool
)


_live_activity_probe = None


def set_live_activity_probe(probe) -> None:
    """Register ``() -> bool`` reporting live background activity.

    The delegate_subagent registry lives in the product package; the status
    line mechanism stays product-neutral by asking through this probe.
    """
    global _live_activity_probe
    _live_activity_probe = probe


def subagents_own_the_status_line() -> bool:
    """Whether a live sub-agent fan-out should suppress the witty status line.

    The pinned ``Agents`` panel is already the standing view of the fan-out: it
    names every running sub-agent, what it is doing right now, its turns, its
    tokens and its duration. A witty line printed above that panel says the
    same thing less precisely — and because the two rotate on independent
    timers they visibly contradict each other, with the line reading "asking
    the user" while the row directly beneath it reads ``grep``.

    So while anything is in flight, the status line keeps its spinner and its
    ``esc to interrupt`` hint, which are facts, and drops the quip, which is
    not. This is the same reasoning that defers an idle recap during a
    fan-out: the user is not watching an idle screen.

    The registry itself lives in the product package; this module only asks
    through the probe it registers on import.
    """
    if _live_activity_probe is None:
        return False
    return bool(_live_activity_probe())


def _pick_from_pool(pool: tuple[str, ...], exclude: str | None = None) -> str:
    """Pick a line from a pool, avoiding `exclude` if possible."""
    if not pool:
        return ""
    if len(pool) <= 1:
        return pool[0]
    choice = random.choice(pool)
    if exclude is None or choice != exclude:
        return choice
    candidates = [line for line in pool if line != exclude]
    return random.choice(candidates) if candidates else choice


def pick_agent_status_line(state: str = "thinking", exclude: str | None = None) -> str:
    """Pick a witty status line for an agent or model state."""
    pool = (
        AGENT_STATUS_LINES.get(state)
        or AGENT_STATUS_LINES.get("thinking")
        or AGENT_STATUS_LINES["general"]
    )
    return _pick_from_pool(pool, exclude=exclude)


def pick_tool_status_line(tool_name: str, exclude: str | None = None) -> str:
    """Pick a witty status line for an active tool.

    Per-tool quips come from the tool's registered flair
    (:func:`ctx.agent.tools.tool_flair`); unregistered tools get the default
    pool.
    """
    from ctx.agent.tools import tool_flair

    flair = tool_flair(tool_name)
    pool = flair.status_lines if flair and flair.status_lines else DEFAULT_TOOL_STATUS_LINES
    return _pick_from_pool(pool, exclude=exclude)


def pick_tool_error_line(tool_name: str, exclude: str | None = None) -> str:
    """Pick a witty error line when a tool execution fails."""
    from ctx.agent.tools import tool_flair

    flair = tool_flair(tool_name)
    pool = flair.error_lines if flair and flair.error_lines else DEFAULT_TOOL_ERROR_LINES
    return _pick_from_pool(pool, exclude=exclude)


def pick_recap_status_line(exclude: str | None = None) -> str:
    """Pick a witty status line for the recap spinner."""
    return _pick_from_pool(RECAP_STATUS_LINES, exclude=exclude)


def pick_witty_line(
    exclude: str | None = None,
    *,
    tool_name: str | None = None,
    state: str | None = None,
    is_error: bool = False,
) -> str:
    """Universal witty line picker with context awareness."""
    if tool_name:
        if is_error:
            return pick_tool_error_line(tool_name, exclude=exclude)
        return pick_tool_status_line(tool_name, exclude=exclude)
    if state == "recap":
        return pick_recap_status_line(exclude=exclude)
    if state:
        return pick_agent_status_line(state, exclude=exclude)
    if not WITTY_STATUS_LINES:
        return "Thinking..."
    return _pick_from_pool(WITTY_STATUS_LINES, exclude=exclude)
