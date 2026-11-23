"""Prompt-content registry: who provides the persona and extra sections.

``ctx.agent.prompts`` owns the *mechanism* — assembling the final system
prompt. The *content* is contributed by whoever owns the product: a package
registers a base-prompt provider and/or ordered sections, and the builder
joins whatever is registered. Nothing here imports a product.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ctx.agent.context import Context
    from ctx.agent.tools import BaseTool


@dataclass(frozen=True)
class PromptEnv:
    """Everything a registered section may need, resolved once per build."""

    cwd: str
    context: Context | None
    tools: list[BaseTool] | None
    skills: list[Any] | None
    include_git_context: bool
    include_ponytail: bool


@dataclass(frozen=True)
class PromptSection:
    """A named, ordered system-prompt section contributed by a package."""

    name: str
    order: int
    render: Callable[[PromptEnv], str | None]


_base_provider: Callable[[], str] | None = None
_sections: dict[str, PromptSection] = {}


def register_base_prompt(provider: Callable[[], str], *, replace: bool = False) -> None:
    """Set the default base-identity provider.

    ``provider()`` returns the persona/rules string used when the caller does
    not pass ``base_content``. ``replace=True`` overwrites an existing
    provider; otherwise the first registration wins.
    """
    global _base_provider
    if replace or _base_provider is None:
        _base_provider = provider


def base_prompt() -> str:
    """The registered base prompt, or ``""`` when no product provides one."""
    return _base_provider() if _base_provider is not None else ""


def register_prompt_section(
    name: str, render: Callable[[PromptEnv], str | None], *, order: int = 50, replace: bool = False
) -> None:
    """Register (or replace) a system-prompt section.

    Sections render in ascending ``order`` after the base identity and
    ``extra_instructions``. Return ``None``/``""`` to contribute nothing.
    """
    if name in _sections and not replace:
        raise ValueError(f"prompt section {name!r} already registered")
    _sections[name] = PromptSection(name=name, order=order, render=render)


def unregister_prompt_section(name: str) -> None:
    _sections.pop(name, None)


def prompt_sections() -> list[PromptSection]:
    """Registered sections sorted by ``(order, name)``."""
    return sorted(_sections.values(), key=lambda s: (s.order, s.name))
