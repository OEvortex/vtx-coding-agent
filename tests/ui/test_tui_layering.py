"""vtx.tui is one flat package again — the base/product split is gone.

Guards two things: no module may live under vtx.coding_agent.tui any more, and
every name vtx.tui advertises in __all__ must actually resolve.
"""

import importlib
import pkgutil

import pytest

import vtx.tui
from vtx.coding_agent import __path__ as coding_agent_path


def test_no_coding_agent_tui_package() -> None:
    leftovers = [p for p in coding_agent_path if p.endswith("tui")]
    assert not leftovers, f"UI lives in vtx.tui now, found: {leftovers}"


def test_every_submodule_imports() -> None:
    for mod in pkgutil.iter_modules(vtx.tui.__path__):
        if mod.name == "commands":
            for sub in pkgutil.iter_modules(
                (vtx.tui.__path__[0] + "/commands",), "vtx.tui.commands."
            ):
                importlib.import_module(sub.name)
            continue
        importlib.import_module(f"vtx.tui.{mod.name}")


@pytest.mark.parametrize("name", vtx.tui.__all__)
def test_public_name_resolves(name: str) -> None:
    assert getattr(vtx.tui, name) is not None
