"""Host-bridge failure taxonomy for the REPL kernel.

Every failure that crosses the kernel↔host boundary used to reach the model as
a bare Python traceback, so ``No tool named 'goal'`` and ``goal refused: not
your session`` and a host defect all looked like the same class of event. The
model then retried the identical call, or blamed its own cell. The taxonomy
gives each case a category, and each category a different prescribed next step.

The rules under test come from opencode's CodeMode design: a public/private
split (a model-safe message plus a host-only detail), and sanitization by
default for anything unclassified.
"""

from __future__ import annotations

import pytest

from vtx.ai.agent.rlm.diagnostics import (
    EXECUTION_FAILURE,
    HOST_UNAVAILABLE,
    INVALID_INPUT,
    INVALID_OUTPUT,
    KINDS,
    TIMEOUT,
    TOOL_FAILURE,
    UNKNOWN_TOOL,
    BridgeError,
    classify_bridge_error,
    plain_data,
)


class _FakeValidationError(ValueError):
    """Stands in for pydantic's ValidationError, matched by type name."""


_FakeValidationError.__name__ = "ValidationError"


class TestCategories:
    def test_every_kind_has_a_remedy(self):
        for kind in KINDS:
            assert BridgeError(kind, "x").render().count("Next:"), kind

    def test_remedies_differ_by_category(self):
        """A taxonomy that renders the same advice for everything is decoration."""
        remedies = {BridgeError(kind, "x").render().split("Next: ", 1)[1] for kind in KINDS}
        assert len(remedies) == len(KINDS)

    def test_only_timeout_is_retryable(self):
        assert BridgeError(TIMEOUT, "x").retryable is True
        assert BridgeError(TOOL_FAILURE, "x").retryable is False
        assert BridgeError(UNKNOWN_TOOL, "x").retryable is False

    def test_unknown_kind_is_rejected(self):
        with pytest.raises(ValueError, match="unknown bridge failure kind"):
            BridgeError("kaboom", "x")

    def test_render_carries_category_message_and_next_step(self):
        rendered = BridgeError(TOOL_FAILURE, "goal refused: not your session").render()
        assert "[bridge:tool_failure]" in rendered
        assert "goal refused: not your session" in rendered
        assert "Next:" in rendered

    def test_str_carries_the_category(self):
        """The kernel reports exceptions to the model as str(); the kind must ride along."""
        assert "[bridge:invalid_input]" in str(BridgeError(INVALID_INPUT, "bad args"))


class TestClassification:
    def test_tool_name_miss_is_unknown_tool(self):
        err = classify_bridge_error("goal", ValueError("Tool not found: goal"))
        assert err.kind == UNKNOWN_TOOL

    def test_unknown_tool_lists_the_available_names(self):
        err = classify_bridge_error(
            "goal", ValueError("Tool not found: goal"), available=("read", "bash")
        )
        assert "Available tools: bash, read" in err.render()

    def test_pydantic_validation_is_invalid_input(self):
        err = classify_bridge_error("read", _FakeValidationError("Field required: path"))
        assert err.kind == INVALID_INPUT

    def test_validation_error_wins_over_the_not_found_sniff(self):
        """pydantic's ValidationError subclasses ValueError and can say 'not found'."""
        err = classify_bridge_error("read", _FakeValidationError("field not found in model"))
        assert err.kind == INVALID_INPUT

    def test_tool_refusal_is_tool_failure(self):
        err = classify_bridge_error("goal", RuntimeError("goal refused: not your session"))
        assert err.kind == TOOL_FAILURE
        # The tool's own message is authored for the model, so it is forwarded.
        assert "goal refused: not your session" in err.render()

    def test_timeout_is_retryable(self):
        err = classify_bridge_error("bash", TimeoutError("too slow"))
        assert err.kind == TIMEOUT
        assert err.retryable

    def test_unclassified_defect_is_sanitized(self):
        err = classify_bridge_error("read", OSError("/srv/internal/secret: permission denied"))
        assert err.kind == EXECUTION_FAILURE
        rendered = err.render()
        # The type is named; the message is not.
        assert "OSError" in rendered
        assert "secret" not in rendered
        # The host keeps the cause.
        assert "secret" in err.detail

    def test_bridge_error_passes_through_unchanged(self):
        original = BridgeError(HOST_UNAVAILABLE, "no bridge here")
        assert classify_bridge_error("read", original) is original


class TestPlainData:
    def test_json_like_values_pass(self):
        assert plain_data({"a": [1, "two", None, True]}) == {"a": [1, "two", None, True]}

    def test_non_json_values_still_cross_via_str(self):
        """``default=str`` is preserved: a tool returning an object keeps working."""
        assert plain_data(object()).startswith("<object object at")

    def test_circular_structure_is_invalid_output(self):
        circular: dict = {}
        circular["self"] = circular
        with pytest.raises(BridgeError) as excinfo:
            plain_data(circular)
        assert excinfo.value.kind == INVALID_OUTPUT

    def test_broken_dunder_str_does_not_leak_across_the_bridge(self):
        """``default=str`` means a value only fails via ``__str__`` raising.

        That exception used to propagate raw, carrying host paths into context.
        """

        class Secret:
            def __str__(self) -> str:
                raise RuntimeError("nope: /srv/internal/SECRET-TOKEN")

        with pytest.raises(BridgeError) as excinfo:
            plain_data(Secret())
        rendered = excinfo.value.render()
        assert excinfo.value.kind == INVALID_OUTPUT
        assert "SECRET-TOKEN" not in rendered
        # The host keeps the cause.
        assert "SECRET-TOKEN" in excinfo.value.detail

    def test_invalid_output_names_the_type_but_not_the_value(self):
        circular: dict = {}
        circular["self"] = circular
        with pytest.raises(BridgeError) as excinfo:
            plain_data(circular)
        assert "dict" in excinfo.value.render()


class TestKernelRendering:
    def test_bridge_error_event_has_no_python_traceback(self):
        """A categorized answer must not arrive wrapped in host frames."""
        from vtx.ai.agent.rlm.repl import _error_event

        event = _error_event("cell-1", BridgeError(TOOL_FAILURE, "goal refused"))
        assert event["ename"] == "BridgeError[tool_failure]"
        assert event["bridgeKind"] == TOOL_FAILURE
        assert event["retryable"] is False
        assert not any('File "' in line for line in event["traceback"])
        assert any("Next:" in line for line in event["traceback"])

    def test_ordinary_exceptions_keep_their_traceback(self):
        from vtx.ai.agent.rlm.repl import _error_event

        try:
            raise ValueError("ordinary bug")
        except ValueError as exc:
            event = _error_event("cell-2", exc)
        assert event["ename"] == "ValueError"
        assert "bridgeKind" not in event


class TestHostToolCall:
    """End-to-end through the host handler the kernel actually calls."""

    @staticmethod
    def _ctx(executor):
        from vtx.ai.agent.rlm.host import _Ctx
        from vtx.ai.agent.rlm.registry import ChildRegistry

        return _Ctx(session_id="s", tool_executor=executor, registry=ChildRegistry())

    async def _call(self, executor, name="goal", args=None):
        from vtx.ai.agent.rlm.host import _handle_tool_call

        return await _handle_tool_call({"name": name, "args": args or {}}, self._ctx(executor))

    @pytest.mark.asyncio
    async def test_success_passes_plain_data_through(self):
        async def ok(name, args):
            return {"status": "ok"}

        assert await self._call(ok) == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_missing_executor_is_host_unavailable(self):
        with pytest.raises(BridgeError) as excinfo:
            await self._call(None)
        assert excinfo.value.kind == HOST_UNAVAILABLE

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("raised", "expected"),
        [
            (ValueError("Tool not found: goal"), UNKNOWN_TOOL),
            (RuntimeError("goal refused: not your session"), TOOL_FAILURE),
            (TimeoutError(), TIMEOUT),
            (OSError("/srv/internal/secret: denied"), EXECUTION_FAILURE),
        ],
    )
    async def test_executor_failures_are_categorized(self, raised, expected):
        async def failing(name, args):
            raise raised

        with pytest.raises(BridgeError) as excinfo:
            await self._call(failing)
        assert excinfo.value.kind == expected

    @pytest.mark.asyncio
    async def test_host_defect_text_never_reaches_the_model(self):
        async def failing(name, args):
            raise OSError("/srv/internal/SECRET-TOKEN: denied")

        with pytest.raises(BridgeError) as excinfo:
            await self._call(failing)
        assert "SECRET-TOKEN" not in excinfo.value.render()
        assert "SECRET-TOKEN" in excinfo.value.detail

    @pytest.mark.asyncio
    async def test_unserializable_result_is_invalid_output(self):
        async def circular(name, args):
            payload: dict = {}
            payload["self"] = payload
            return payload

        with pytest.raises(BridgeError) as excinfo:
            await self._call(circular)
        assert excinfo.value.kind == INVALID_OUTPUT

    @pytest.mark.asyncio
    async def test_malformed_payload_still_raises_host_error(self):
        """Wire-shape validation is a protocol error, not a tool failure."""
        from vtx.ai.agent.rlm.host import HostError, _handle_tool_call

        with pytest.raises(HostError):
            await _handle_tool_call({"name": "", "args": {}}, self._ctx(None))
