"""Tests for mounting: service-availability ordering, failures, and teardown."""

from __future__ import annotations

import pytest

from vtx.plugins import Context, PluginHost, ServiceMissingError, plugin
from vtx.plugins.loader import Row
from vtx.plugins.plugin import Plugin


def _row(row_id: str, name: str, **kwargs) -> Row:
    return Row(id=row_id, name=name, **kwargs)


def test_a_row_mounts_once_the_service_it_names_exists():
    host = PluginHost([_row("a", "vtx.plugins.builtins.services.tools:PLUGIN")])
    host.mount()
    assert "tools" in host.base


def test_a_row_whose_service_never_appears_fails_the_mount():
    rows = [_row("orphan", "vtx.plugins.builtins.tools.read:PLUGIN")]
    with pytest.raises(ServiceMissingError, match="orphan"):
        PluginHost(rows).mount()


def test_mount_order_follows_service_availability_not_row_order():
    # The consumer is listed first; it must still mount after its provider.
    rows = [
        _row("consumer", "vtx.plugins.builtins.tools.read:PLUGIN"),
        _row("provider", "vtx.plugins.builtins.services.tools:PLUGIN"),
    ]
    host = PluginHost(rows)
    host.mount()
    assert host.mounted_ids() == ["provider", "consumer"]


def test_a_disabled_row_does_not_mount():
    host = PluginHost([_row("a", "vtx.plugins.builtins.services.tools:PLUGIN", disabled=True)])
    host.mount()
    assert host.mounted_ids() == []


def test_dispose_unwinds_every_row_and_removes_provided_services():
    host = PluginHost([_row("a", "vtx.plugins.builtins.services.tools:PLUGIN")])
    host.mount()
    assert "tools" in host.base
    host.dispose()
    assert host.mounted_ids() == []
    assert "tools" not in host.base


def test_unmount_takes_one_row_and_leaves_the_rest():
    rows = [
        _row("tools", "vtx.plugins.builtins.services.tools:PLUGIN"),
        _row("read", "vtx.plugins.builtins.tools.read:PLUGIN"),
    ]
    host = PluginHost(rows)
    host.mount()
    assert host.mounted_ids() == ["tools", "read"]
    assert host.unmount("read") is True
    assert host.unmount("read") is False
    assert host.mounted_ids() == ["tools"]


def test_status_reports_each_row_once():
    rows = [
        _row("tools", "vtx.plugins.builtins.services.tools:PLUGIN"),
        _row("off", "vtx.plugins.builtins.services.llm:PLUGIN", disabled=True),
    ]
    host = PluginHost(rows)
    host.mount()
    status = {record["id"]: record for record in host.status()}
    assert status["tools"]["mounted"] is True
    assert status["off"]["mounted"] is False
    assert status["off"]["disabled"] is True


def test_a_row_effect_is_unwound_by_dispose():
    torn: list[str] = []

    @plugin(name="effectful")
    def apply(ctx, config):
        ctx.effect(lambda: torn.append("gone"))

    with Context().scoped() as scope:
        apply.apply(scope, None)
    assert torn == ["gone"]


def test_a_plugin_that_registers_an_event_is_unwound():
    import asyncio

    seen: list[str] = []

    @plugin(name="listener")
    def apply(ctx, config):
        def handler(payload: str) -> None:
            seen.append(payload)

        ctx.on("thing", handler)

    with Context().scoped() as scope:
        apply.apply(scope, None)
        asyncio.run(scope.hub.emit("thing", "x"))
    assert seen == ["x"]
    asyncio.run(Context().hub.emit("thing", "y"))
    assert seen == ["x"]


def test_an_unimportable_row_fails_at_load_not_at_mount():
    host = PluginHost([_row("broken", "vtx.plugins.nope:PLUGIN")])
    with pytest.raises(Exception, match="cannot import"):
        host.mount()


def test_a_plugin_object_can_be_mounted_without_yaml():
    first = Plugin(name="one", apply=lambda ctx, config: ctx.provide("k", "first"))
    second = Plugin(name="two", apply=lambda ctx, config: ctx.provide("k", "second"))
    rows = [_row("one", "one"), _row("two", "two", disabled=False)]
    resolver = lambda name: {"one": first, "two": second}[name]  # noqa: E731
    host = PluginHost(rows, resolver=resolver)
    host.mount()
    # The later row wins, which is the documented last-write rule.
    assert host.base.get("k") == "second"
    host.dispose()
    assert "k" not in host.base


def test_compose_mounts_in_one_step():
    from vtx.plugins import compose

    host = compose([_row("a", "vtx.plugins.builtins.services.tools:PLUGIN")])
    assert "tools" in host.base
    host.dispose()
