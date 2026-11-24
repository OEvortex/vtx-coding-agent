"""Session titles: one cheap call per session, off the turn's critical path.

A transcript's first prompt is a poor label — "yo list all ur tools" says less
than "tool inventory" — so vx asks the model for a two-to-five word title once
a session has a reply to name it by. The call never blocks a turn: it is
scheduled when a turn ends and applied whenever it comes back.

Nothing here is fatal on failure: an untitled session keeps its first prompt in
the picker, which is what it had before.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ctx.protocol.abc import BaseProvider
from ctx.protocol.types import TextPart, UserMessage

#: Long enough to describe the work, short enough for a picker row.
MAX_TITLE_CHARS = 60

#: What the naming call is asked for. It gets the opening exchange, not the
#: whole session: the first prompt and reply are what a title is about.
TITLE_PROMPT = (
    "Name this coding session in 2 to 5 words, the way a developer would label "
    "a tab. Use the language and the task, not the wording. Reply with the "
    "title only: no quotes, no full stop, no explanation."
)

#: Characters of transcript the naming call sees.
SEED_CHARS = 1200

#: Markdown and quoting a model wraps a title in, to be trimmed off both ends.
_TITLE_TRIM = "`*_#'\"'. -"


def needs_title(session: Any, attempted: set[str]) -> bool:
    """Whether this session should be named: once, after it has a reply."""
    if session is None or session.id in attempted:
        return False
    if getattr(session, "name", None):
        return False
    messages = getattr(session, "messages", None) or []
    return len(messages) >= 2


def seed(session: Any, limit: int = SEED_CHARS) -> str:
    """The opening exchange, flattened to what a title needs."""
    parts: list[str] = []
    for message in (getattr(session, "messages", None) or [])[:4]:
        role = getattr(message, "role", "")
        content = getattr(message, "content", "")
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(p.text for p in content if isinstance(getattr(p, "text", None), str))
        text = " ".join(text.split())
        if not text:
            continue
        parts.append(f"{'user' if role == 'user' else 'assistant'}: {text}")
        if sum(len(p) for p in parts) >= limit:
            break
    return "\n".join(parts)[:limit]


def clean_title(text: str) -> str | None:
    """A title fit for a picker row, or None when the model said nothing useful."""
    line = ""
    for candidate in text.splitlines():
        candidate = " ".join(candidate.split()).strip(_TITLE_TRIM)
        if candidate:
            line = candidate
            break
    if not line:
        return None
    if len(line) > MAX_TITLE_CHARS:
        line = line[:MAX_TITLE_CHARS].rstrip(" ,;:-") + "…"
    # A model that answers with a paragraph is not a title; the first clause is
    # the closest thing to one.
    if len(line.split()) > 10:
        line = line.split(".")[0].strip()
        line = line[:MAX_TITLE_CHARS]
    return line or None


async def generate_title(provider: BaseProvider, session: Any) -> str | None:
    """Ask the model for a title. Returns None on any failure, by design."""
    opening = seed(session)
    if not opening:
        return None
    stream = await provider.stream(
        [UserMessage(content=f"{opening}\n\n{TITLE_PROMPT}")], system_prompt=None, tools=None
    )
    chunks: list[str] = []
    async for part in stream:
        if isinstance(part, TextPart):
            chunks.append(part.text)
    return clean_title("".join(chunks))


async def title_session(
    provider: BaseProvider, session: Any, *, timeout: float = 25.0
) -> str | None:
    """Generate a title with a deadline; a slow title is no title."""
    try:
        return await asyncio.wait_for(generate_title(provider, session), timeout)
    except Exception:
        return None
