"""Row-cache plumbing: the paint generation and the revision blocks carry.

A block's rows depend on two things the block does not own: the fields it was
built from, and module-level state — the active theme and ctrl+o's expansion.
Caching rendered rows therefore needs both a per-block revision (so a streamed
delta re-renders only the block that changed) and a global generation (so a
theme switch or an expansion toggle re-renders every block at once). Everything
that mutates those globals bumps the generation; the viewport treats a stale
generation as a miss.
"""

from __future__ import annotations

_generation = 0

#: Distinguishes "no such attribute" from an attribute that happens to be None.
_MISSING = object()


def generation() -> int:
    """The current paint generation: rows built under an older one are stale."""
    return _generation


def bump_generation() -> None:
    """Announce a change to state every block renders against."""
    global _generation
    _generation += 1


class Revisioned:
    """An object that counts its own mutations.

    Assignment through the class bumps ``_rev``, so a row cache can tell "this
    block changed" without re-rendering it to find out. Identity is the test:
    a value that is the same object cannot have changed in a way a render would
    see, and an in-place mutation of a container is caught by the owners that
    care (see ``AskItem.signature``), not by every assignment.
    """

    _rev: int = 0

    def __setattr__(self, name: str, value: object) -> None:
        if name != "_rev" and self.__dict__.get(name, _MISSING) is not value:
            object.__setattr__(self, "_rev", self.__dict__.get("_rev", 0) + 1)
        object.__setattr__(self, name, value)

    def signature(self) -> tuple:
        """What a cache must see unchanged to reuse this object's rows."""
        return (self._rev,)
