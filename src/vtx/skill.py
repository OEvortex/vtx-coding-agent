"""Python skill module wrapper and CLI runner for VTX Python-backed skills.

Follows the Prime Agent Python skills protocol:
- If a skill package defines `run(...)` (sync or async), the module is wrapped
  into a callable object so `await skill(...)` and `await skill.run(...)` work.
- Signature and docstring from `run(...)` are copied so `help(skill)` displays the API.
- `cli()` entrypoint parses sys.argv and executes the skill from CLI/shell cells.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import inspect
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any


class CallableModule(ModuleType):
    """Wraps a Python skill module so that calling the module invokes `run()`."""

    def __init__(self, module: ModuleType, run_func: Callable[..., Any]) -> None:
        super().__init__(module.__name__)
        self.__dict__.update(module.__dict__)
        self.__module_obj__ = module
        self.__run_func__ = run_func
        with contextlib.suppress(Exception):
            self.__signature__ = inspect.signature(run_func)
        doc = getattr(run_func, "__doc__", None) or getattr(module, "__doc__", None)
        if doc:
            self.__doc__ = doc

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        res = self.__run_func__(*args, **kwargs)
        if inspect.isawaitable(res):
            return res
        return res

    def __repr__(self) -> str:
        file_path = getattr(self, "__file__", None)
        return f"<python skill {self.__name__!r} from {file_path!r}>"


class FailedSkillModule:
    """Placeholder for a Python skill that failed to import."""

    def __init__(self, name: str, error: Exception) -> None:
        self.__name__ = name
        self.__error__ = error
        self.__doc__ = f"Failed to import skill {name!r}: {error}"

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(f"Python skill {self.__name__!r} failed to import: {self.__error__}")

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(f"Python skill {self.__name__!r} failed to import: {self.__error__}")

    def __repr__(self) -> str:
        return f"<failed python skill {self.__name__!r}: {self.__error__}>"


def wrap_skill_module(module: ModuleType) -> Any:
    """Wrap a skill module so calling it invokes `module.run` if present."""
    run_func = getattr(module, "run", None)
    if callable(run_func):
        return CallableModule(module, run_func)
    return module


def cli() -> None:
    """Run `<skill>.run` for a console script named exactly after the skill import."""
    prog = Path(sys.argv[0]).stem
    try:
        module = __import__(prog)
    except ImportError as exc:
        raise RuntimeError(
            f"Could not import Python skill module {prog!r}. "
            "The console-script name must match the skill import name exactly; "
            "use underscores instead of dashes."
        ) from exc
    run = getattr(module, "run", None)
    if not callable(run):
        raise RuntimeError(f"{prog} does not expose a callable run()")

    sig = inspect.signature(run)
    parser = argparse.ArgumentParser(prog=prog, description=run.__doc__ or f"{prog} skill")

    positional_params = []
    keyword_params = []
    for param in sig.parameters.values():
        if param.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            if param.default is inspect.Parameter.empty:
                positional_params.append(param)
            else:
                keyword_params.append(param)
        elif param.kind == inspect.Parameter.KEYWORD_ONLY:
            keyword_params.append(param)

    for param in positional_params:
        p_type = param.annotation if param.annotation in (int, float, str, bool) else str
        parser.add_argument(param.name, type=p_type, help=f"{param.name}")

    for param in keyword_params:
        flag = f"--{param.name.replace('_', '-')}"
        p_type = param.annotation if param.annotation in (int, float, str, bool) else str
        if param.annotation is bool:
            if param.default is True:
                parser.add_argument(
                    f"--no-{param.name.replace('_', '-')}",
                    dest=param.name,
                    action="store_false",
                    default=True,
                )
            else:
                parser.add_argument(flag, dest=param.name, action="store_true", default=False)
        else:
            parser.add_argument(
                flag, type=p_type, default=param.default, help=f"Default: {param.default}"
            )

    args = parser.parse_args(sys.argv[1:])
    kwargs = vars(args)

    result = run(**kwargs)
    if inspect.isawaitable(result):
        result = asyncio.run(result)
    if result is not None:
        print(result)
