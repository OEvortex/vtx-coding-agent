"""Swarm support for vx: a shared board and the agents that talk on it."""

from __future__ import annotations

from vx.swarm.board import Board, MemberRow, Post, render_posts
from vx.swarm.engine import (
    MAX_MEMBERS,
    Member,
    SwarmEngine,
    SwarmError,
    close_all_engines,
    current_engine,
    engine_for,
    forget_engine,
    peek_engine,
)
from vx.swarm.mentions import ALL, COORDINATOR, addressed, group_of

__all__ = [
    "ALL",
    "COORDINATOR",
    "MAX_MEMBERS",
    "Board",
    "Member",
    "MemberRow",
    "Post",
    "SwarmEngine",
    "SwarmError",
    "addressed",
    "close_all_engines",
    "current_engine",
    "engine_for",
    "forget_engine",
    "group_of",
    "peek_engine",
    "render_posts",
]
