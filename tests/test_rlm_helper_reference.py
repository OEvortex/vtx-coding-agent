"""The documented helper reference must match the helpers the kernel binds.

The RLM prompt used to carry a hand-maintained signature table. Nothing tied it
to ``_init_builtin_helpers``, so adding a helper without documenting it made it
invisible to the model, and changing a signature left the prompt advertising an
argument list the kernel no longer accepted. Both failures are silent: the model
reads a plausible, wrong reference and calls accordingly.

These tests bind the real helpers and compare them against the generated
reference, so drift is a test failure instead.
"""

from __future__ import annotations

import inspect

import pytest

from vtx.ai.agent.prompts.rlm import _RLM_HELPERS_PROMPT
from vtx.ai.agent.rlm import repl as repl_module
from vtx.ai.agent.rlm.helper_docs import HELPER_SPECS, OBJECTS, render_helpers_reference


@pytest.fixture(scope="module")
def bound() -> dict[str, object]:
    """The helpers the kernel actually binds, in a throwaway namespace."""
    original = repl_module._namespace
    repl_module._namespace = {}
    try:
        repl_module._init_builtin_helpers()
        return dict(repl_module._namespace)
    finally:
        repl_module._namespace = original


def _parameters(signature: str) -> list[tuple[str, str | None]]:
    """``(name, default)`` pairs from a prompt-style call signature.

    Annotations are ignored deliberately: the reference is model-facing, so it
    carries names and defaults only, and ``from __future__ import annotations``
    makes the real ones render as quoted strings that would never match prose.
    What must agree is the *call shape* — which arguments exist, in what order,
    and which are optional. Parameter *kind* (``*args`` / ``**kwargs``) is
    normalised away, since the prompt writes it for readability and the kernel
    declares it for dispatch.
    """
    inner = signature[signature.index("(") + 1 : signature.rindex(")")]
    if not inner.strip():
        return []
    parts: list[tuple[str, str | None]] = []
    for raw in inner.split(","):
        piece = raw.strip().lstrip("*")
        if not piece:
            continue
        name, separator, default = piece.partition("=")
        parts.append((name.strip(), default.strip() if separator else None))
    return parts


def _real_parameters(func: object) -> list[tuple[str, str | None]]:
    return [
        (param.name, None if param.default is inspect.Parameter.empty else repr(param.default))
        for param in inspect.signature(func).parameters.values()
    ]


def test_every_bound_helper_is_documented(bound):
    """Depends on ``bound`` so the table has actually been populated."""
    documented = {spec.name for spec in HELPER_SPECS}
    # ``_HELPERS`` is the single binding table; anything in it that the model is
    # not told about is a helper the model cannot know exists.
    bound_names = {name for name, _ in repl_module._HELPERS}
    assert bound_names == documented, (
        "helpers bound but not documented: "
        f"{sorted(bound_names - documented)}; "
        f"documented but not bound: {sorted(documented - bound_names)}"
    )


@pytest.mark.parametrize("spec", HELPER_SPECS, ids=lambda spec: spec.name)
def test_each_documented_signature_matches_the_bound_helper(spec, bound):
    """A signature the kernel does not accept is the exact bug this prevents."""
    helper = bound.get(spec.name)
    assert helper is not None, f"{spec.name} is documented but not bound"
    assert callable(helper), f"{spec.name} is documented as callable but is not"
    if not spec.verify_signature:
        return
    documented = _parameters(spec.signature)
    actual = _real_parameters(helper)
    assert [name for name, _ in documented] == [name for name, _ in actual], (
        f"{spec.name}: prompt lists {[n for n, _ in documented]}, "
        f"kernel accepts {[n for n, _ in actual]}"
    )
    assert [_normalize_default(default) for _, default in documented] == [
        _normalize_default(default) for _, default in actual
    ], f"{spec.name}: prompt defaults differ from the kernel's"


def test_only_the_wrapper_helper_skips_signature_verification():
    """Keeps the exemption from quietly spreading."""
    skipped = {spec.name for spec in HELPER_SPECS if not spec.verify_signature}
    assert skipped == {"host_request"}


def test_every_bound_helper_is_protected(bound):
    """A helper that is bound but not guarded can be silently disarmed."""
    protected = set(repl_module._protected_helpers)
    assert {name for name, _ in repl_module._HELPERS} <= protected


def _normalize_default(default: str | None) -> str | None:
    """Compare ``180`` and ``180.0`` as equal; the prompt rounds for readability."""
    if default is None:
        return None
    try:
        return repr(float(default))
    except ValueError:
        return default


def test_the_prompt_section_is_the_generated_one():
    assert _RLM_HELPERS_PROMPT == render_helpers_reference()


def test_every_helper_appears_in_the_prompt():
    for spec in HELPER_SPECS:
        assert f"`{spec.signature}`" in _RLM_HELPERS_PROMPT, spec.name


def test_the_discovery_helpers_are_documented():
    """The Gap 5 additions, so the model knows they exist."""
    for name in ("find_tools", "describe_tool"):
        assert any(spec.name == name for spec in HELPER_SPECS)


def test_non_callable_names_are_described_without_a_signature():
    """Inventing a signature for ``rlm`` would be a lie the model could act on."""
    for name, _summary in OBJECTS:
        assert f"`{name}`" in _RLM_HELPERS_PROMPT
    assert "`rlm(`" not in _RLM_HELPERS_PROMPT


def test_the_shadowing_notice_is_present():
    assert "cannot be reassigned" in _RLM_HELPERS_PROMPT


def test_the_not_callable_note_is_present():
    assert "not callable" in _RLM_HELPERS_PROMPT


def test_no_duplicate_helper_names():
    names = [spec.name for spec in HELPER_SPECS]
    assert len(names) == len(set(names))


def test_no_duplicate_signatures():
    signatures = [spec.signature for spec in HELPER_SPECS]
    assert len(signatures) == len(set(signatures))


def test_every_spec_documents_a_return_and_a_summary():
    for spec in HELPER_SPECS:
        assert spec.returns.strip(), spec.name
        assert spec.summary.strip(), spec.name


def test_the_prompt_section_stays_a_reasonable_size():
    """It competes with the bridge contract and the harness reference."""
    assert len(_RLM_HELPERS_PROMPT) < 6000
