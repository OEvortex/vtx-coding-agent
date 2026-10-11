"""Tests for plugin rows, patch layering, and config validation."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from vtx.plugins import (
    ConfigError,
    Context,
    DuplicateRowError,
    PatchError,
    Row,
    RowError,
    apply_patch,
    resolve_plugin,
    rows_from_layer,
    validate_config,
)
from vtx.plugins.plugin import Plugin, Service, as_plugin, plugin


def test_row_round_trips_to_dict():
    row = Row(id="llm", name="vtx.plugins.builtins.services.llm:PLUGIN", config={"a": 1})
    assert row.to_dict() == {
        "id": "llm",
        "name": "vtx.plugins.builtins.services.llm:PLUGIN",
        "disabled": False,
        "config": {"a": 1},
    }
    assert Row(id="a", name="b").to_dict()["config"] == {}
    assert "meta" not in Row(id="a", name="b").to_dict()


def test_row_requires_an_id():
    with pytest.raises(RowError, match="`id`"):
        rows_from_layer([{"name": "x"}], layer_name="t")


def test_row_requires_a_name():
    with pytest.raises(RowError, match="`name`"):
        rows_from_layer([{"id": "x"}], layer_name="t")


def test_row_rejects_unknown_keys():
    with pytest.raises(RowError, match="unknown keys"):
        rows_from_layer([{"id": "x", "name": "y", "nope": 1}], layer_name="t")


def test_duplicate_ids_in_one_layer_fail_loud():
    with pytest.raises(DuplicateRowError, match="dup"):
        rows_from_layer([{"id": "dup", "name": "a"}, {"id": "dup", "name": "b"}], layer_name="t")


def test_bare_list_of_rows_is_a_valid_layer():
    rows = rows_from_layer([{"id": "a", "name": "m"}], layer_name="t")
    assert [row.id for row in rows] == ["a"]


def test_insert_appends_and_replaces_wholesale():
    base = rows_from_layer([{"id": "a", "name": "m", "config": {"x": 1}}], layer_name="t")
    patched = apply_patch(base, [{"insert": [{"id": "b", "name": "n"}]}])
    assert [row.id for row in patched] == ["a", "b"]

    replaced = apply_patch(patched, [{"insert": [{"id": "a", "name": "other"}]}])
    target = next(row for row in replaced if row.id == "a")
    assert target.name == "other"
    assert target.config == {}


def test_set_replaces_a_row_the_same_way_as_insert():
    base = rows_from_layer([{"id": "a", "name": "m", "config": {"x": 1}}], layer_name="t")
    patched = apply_patch(base, [{"set": [{"id": "a", "name": "other", "config": {"y": 2}}]}])
    assert patched[0].config == {"y": 2}


def test_override_merges_only_the_fields_it_names():
    base = rows_from_layer([{"id": "a", "name": "m", "config": {"x": 1, "y": 2}}], layer_name="t")
    patched = apply_patch(base, [{"override": [{"id": "a", "config": {"y": 9}}]}])
    assert patched[0].config == {"x": 1, "y": 9}
    assert patched[0].name == "m"


def test_replace_requires_an_existing_row():
    base = rows_from_layer([{"id": "a", "name": "m"}], layer_name="t")
    with pytest.raises(PatchError, match="replace targets unknown"):
        apply_patch(base, [{"replace": [{"id": "zz", "name": "n"}]}])


def test_override_requires_an_existing_row():
    base = rows_from_layer([{"id": "a", "name": "m"}], layer_name="t")
    with pytest.raises(PatchError, match="override targets unknown"):
        apply_patch(base, [{"override": [{"id": "zz", "disabled": True}]}])


def test_remove_drops_the_row_and_rejects_an_unknown_id():
    base = rows_from_layer([{"id": "a", "name": "m"}, {"id": "b", "name": "n"}], layer_name="t")
    patched = apply_patch(base, [{"remove": ["a"]}])
    assert [row.id for row in patched] == ["b"]
    with pytest.raises(PatchError, match="cannot remove unknown"):
        apply_patch(patched, [{"remove": ["a"]}])


def test_override_is_rejected_in_a_bundle_layer():
    with pytest.raises(PatchError, match="override"):
        rows_from_layer([{"override": [{"id": "a", "disabled": True}]}], layer_name="t")


def test_one_operation_per_entry():
    with pytest.raises(PatchError, match="one operation"):
        apply_patch([], [{"insert": [], "remove": []}])


def test_patch_layer_must_be_a_list():
    with pytest.raises(PatchError, match="must be a list"):
        apply_patch([], {"id": "a"})


def test_patch_order_is_preserved_and_new_rows_append():
    base = rows_from_layer([{"id": "a", "name": "m"}, {"id": "b", "name": "n"}], layer_name="t")
    patched = apply_patch(base, [{"insert": [{"id": "c", "name": "o"}]}])
    assert [row.id for row in patched] == ["a", "b", "c"]


class _Config(BaseModel):
    answer: int
    label: str = "x"


def test_validate_config_returns_the_model():
    spec = Plugin(name="p", apply=lambda c, cfg: None, config=_Config)
    row = Row(id="p", name="p", config={"answer": 42})
    validated = validate_config(spec, row)
    assert isinstance(validated, _Config)
    assert validated.answer == 42


def test_validate_config_reports_every_error_with_the_row_id():
    spec = Plugin(name="p", apply=lambda c, cfg: None, config=_Config)
    row = Row(id="bad-row", name="p", config={"answer": "not-a-number"})
    with pytest.raises(ConfigError, match="bad-row") as excinfo:
        validate_config(spec, row)
    assert "answer" in str(excinfo.value)


def test_a_plugin_with_no_schema_accepts_any_mapping():
    spec = Plugin(name="p", apply=lambda c, cfg: None)
    row = Row(id="p", name="p", config={"anything": [1, 2]})
    assert validate_config(spec, row) == {"anything": [1, 2]}


def test_resolve_plugin_reads_the_module_plugin_export():
    spec = resolve_plugin("vtx.plugins.builtins.services.tools:PLUGIN")
    assert spec.name == "tools"
    assert spec.inject == ()


def test_resolve_plugin_reports_an_unimportable_module():
    with pytest.raises(RowError, match="cannot import"):
        resolve_plugin("vtx.plugins.does_not_exist:PLUGIN")


def test_resolve_plugin_reports_a_missing_export():
    with pytest.raises(RowError, match="exposes no"):
        resolve_plugin("vtx.plugins.builtins.services.tools:NOPE")


def test_decorator_records_name_inject_and_meta():
    @plugin(name="thing", inject=("tools",), config=_Config, owner="me")
    def apply(ctx, config):
        return None

    assert apply.name == "thing"
    assert apply.inject == ("tools",)
    assert apply.config is _Config
    assert apply.meta == {"owner": "me"}


def test_as_plugin_accepts_a_plain_apply_function():
    def apply(ctx, config):
        return None

    spec = as_plugin(apply)
    assert spec.name == "apply"
    assert spec.apply is apply


class _Service(Service):
    name = "svc"
    inject = ("tools",)

    def start(self, ctx, config):
        return "started"


def test_as_plugin_accepts_a_service_subclass():
    spec = as_plugin(_Service())
    assert spec.name == "svc"
    assert spec.inject == ("tools",)
    ctx = Context()
    assert spec.apply(ctx, None) == "started"


def test_as_plugin_rejects_a_non_plugin():
    with pytest.raises(TypeError, match="is not a plugin"):
        as_plugin(42)


def test_row_disabled_accepts_a_literal():
    assert Row(id="a", name="b", disabled=True).is_disabled() is True
    assert Row(id="a", name="b", disabled=False).is_disabled() is False
