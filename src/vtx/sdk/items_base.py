"""Base class for SDK RunItem variants."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar

T = TypeVar("T")


@dataclass
class RunItemBase[T]:
    """Common base for SDK ``RunItem`` variants.

    Holds the agent that produced the item plus the raw Vtx message or
    tool call. Subclasses add a ``type`` discriminator and convenience
    accessors.
    """

    agent: Any  # type: ignore[type-arg]
    raw_item: T
