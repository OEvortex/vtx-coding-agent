"""vx — a minimal coding agent built on ``ctx`` and ``ctx.term``."""

from __future__ import annotations

from ctx.agent.context.skills import register_skills_package

# Skills shipped in the package (``builtin_skills/``) reach the model through
# this registration alone: ctx copies them into ``~/.ctx/skills`` for reading and
# merges them into the prompt catalog and the ``/`` commands. Eager, like
# ``ctx.code``'s: the loader runs during startup, before anything else here is
# imported, and a lazy registration would arrive after the catalog was built.
register_skills_package("vx")
