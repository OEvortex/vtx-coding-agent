"""Tests that the REPL restores helpers a cell rebound.

The kernel namespace is one dict for the life of the process, and the bridge
(``call_tool``, ``host_request``), ``bash``, and the file helpers are reachable
*only* through their pre-bound names. ``_init_builtin_helpers`` uses
``setdefault``, so it runs once and never re-binds: a cell that reassigns
``call_tool`` silently disarms it for every later cell, and nothing reports it
until a much later cell fails to reach a tool at all.

These tests exercise ``_restore_shadowed_helpers`` directly against a
throwaway namespace. The subprocess is never spawned.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from vtx.ai.agent.rlm import repl as repl_module

#: Mirrors the ``_HELPERS`` binding table in ``repl.py``. The equality assertion
#: in ``test_every_helper_is_bound_and_protected_by_one_table`` is what keeps
#: this list from drifting into a name that is protected but never bound.
PROTECTED = (
    "bash",
    "run_bash",
    "read_file",
    "write_file",
    "edit_file",
    "run_code",
    "rerun",
    "find_tools",
    "describe_tool",
    "web_search",
    "goal_get",
    "goal_update",
    "goal_set_tasks",
    "call_tool",
    "emit",
    "host_request",
)


@pytest.fixture
def ns() -> dict[str, object]:
    """A namespace holding the real helper objects under their real names."""
    namespace: dict[str, object] = {}
    for name in PROTECTED:
        namespace[name] = _sentinel(name)
    repl_module._protect_helpers(namespace, PROTECTED)
    return namespace


def _sentinel(name: str) -> object:
    return object()


def _declared_helper_names() -> set[str]:
    """The names in the ``_HELPERS`` table that drives bindings and the guard."""
    source = Path(repl_module.__file__).read_text(encoding="utf-8")
    table = re.search(
        r"_HELPERS: tuple\[tuple\[str, Any\], \.\.\.\] = \((.*?)\n    \)", source, re.S
    )
    assert table, "the _HELPERS binding table was not found in repl.py"
    declared = set(re.findall(r'\("([a-z_]+)", ', table.group(1)))
    assert declared, "the _HELPERS table declared no helpers"
    return declared


def test_every_helper_is_bound_and_protected_by_one_table():
    """Bindings and the shadowing guard come from one table, so they cannot drift.

    A name protected while unbound is the failure this prevents: the guard would
    "restore" it to ``None`` on the first cell, silently deleting a working
    helper. Deriving the bindings and the protected set from one table makes
    that unrepresentable rather than merely unlikely.
    """
    assert _declared_helper_names() == set(PROTECTED)
    # Spot-check the newest additions so a rename cannot pass unnoticed.
    assert {"find_tools", "describe_tool", "call_tool", "host_request"} <= set(PROTECTED)


def test_rebinding_a_helper_is_reverted_and_reported(ns: dict[str, object]):
    original = ns["call_tool"]
    ns["call_tool"] = lambda *a, **k: "silently wrong"

    restored = repl_module._restore_shadowed_helpers(ns)

    assert restored == ["call_tool"]
    assert ns["call_tool"] is original


def test_deleting_a_helper_puts_it_back(ns: dict[str, object]):
    original = ns["bash"]
    del ns["bash"]

    restored = repl_module._restore_shadowed_helpers(ns)

    assert restored == ["bash"]
    assert ns["bash"] is original


def test_several_shadowed_helpers_are_all_reverted(ns: dict[str, object]):
    originals = {name: ns[name] for name in ("bash", "emit", "read_file")}
    for name in originals:
        ns[name] = "clobbered"

    restored = repl_module._restore_shadowed_helpers(ns)

    assert set(restored) == set(originals)
    for name, original in originals.items():
        assert ns[name] is original


def test_untouched_namespace_reports_nothing(ns: dict[str, object]):
    assert repl_module._restore_shadowed_helpers(ns) == []


def test_a_live_helper_is_never_replaced_with_none():
    """A guard entry that was never bound must not delete a live helper.

    ``_protect_helpers`` records whatever the namespace held at install time.
    If a name were protected before it was bound, the recorded original would be
    ``None`` and the guard would delete the working helper on the first cell.
    The single binding table prevents that; this asserts the consequence.
    """
    namespace: dict[str, object] = {}
    repl_module._protect_helpers(namespace, ("call_tool",))
    live = _sentinel("call_tool")
    namespace["call_tool"] = live

    repl_module._restore_shadowed_helpers(namespace)

    assert namespace["call_tool"] is live


def test_user_names_are_never_touched(ns: dict[str, object]):
    """Only the pre-bound helper names are protected."""
    ns["my_helper"] = lambda: 1
    ns["data"] = {"a": 1}

    restored = repl_module._restore_shadowed_helpers(ns)

    assert restored == []
    assert ns["my_helper"] is not None
    assert ns["data"] == {"a": 1}


def test_ordinary_reassignment_of_a_local_is_preserved(ns: dict[str, object]):
    """A cell redefining its own local must not trip the guard."""
    ns["result"] = 1
    ns["result"] = 2

    assert repl_module._restore_shadowed_helpers(ns) == []
    assert ns["result"] == 2


def test_rebinding_to_the_same_object_is_not_a_rebind(ns: dict[str, object]):
    """Aliasing a helper under its own name is a no-op, not a shadow."""
    original = ns["rerun"]
    ns["rerun"] = original

    assert repl_module._restore_shadowed_helpers(ns) == []
    assert ns["rerun"] is original
