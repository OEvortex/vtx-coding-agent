"""JSON Schema to pydantic, for tools whose parameters arrive as a schema.

Two callers, one implementation: extensions supply a schema at
``api.register_tool`` time, and MCP servers supply one per tool in
``tools/list``. Both need the same generated ``BaseModel`` so the agent loop's
existing ``tool.params(**arguments)`` validation path works unchanged.

We support the subset of JSON Schema that providers accept: ``type: object``
with ``properties`` and ``required``. Property types map to native Python /
pydantic types. Anything unrecognised becomes ``Any`` -- a looser contract, but
one that never blocks a tool from being called.
"""

from __future__ import annotations

import re
from types import NoneType
from typing import Any

from pydantic import BaseModel, Field, create_model

# JSON Schema keywords that survive a round-trip into the generated model's
# schema via ``json_schema_extra``, so the provider still sees the constraint.
_CONSTRAINT_KEYS = ("enum", "minLength", "maxLength", "minimum", "maximum", "pattern")


def json_type_to_python(prop_schema: Any) -> Any:
    """Map a single property's JSON Schema to a Python type annotation."""
    if not isinstance(prop_schema, dict):
        return Any

    json_type = prop_schema.get("type")

    if isinstance(json_type, list):
        # Nullable unions ("type": ["string", "null"]) -- pick the first non-null.
        for candidate in json_type:
            if candidate != "null":
                json_type = candidate
                break

    if json_type == "string":
        # We deliberately do not translate ``enum`` into a Python ``Literal``:
        # pydantic does not enforce enums from a JSON schema, and the
        # constraint round-trips to the provider through ``json_schema_extra``
        # anyway, which is where it is actually checked.
        return str
    if json_type == "integer":
        return int
    if json_type == "number":
        return float
    if json_type == "boolean":
        return bool
    if json_type == "array":
        return list[json_type_to_python(prop_schema.get("items") or {})]  # ty: ignore[invalid-type-form]
    if json_type == "object":
        return dict[str, Any]
    if json_type == "null":
        return None
    if "anyOf" in prop_schema or "oneOf" in prop_schema:
        branches = prop_schema.get("anyOf") or prop_schema.get("oneOf") or []
        non_null = [b for b in branches if isinstance(b, dict) and b.get("type") != "null"]
        if len(non_null) == 1:
            return json_type_to_python(non_null[0])
        if non_null:
            return Any
    return Any


def safe_class_name(tool_name: str) -> str:
    cleaned = "".join(c if c.isalnum() else "_" for c in tool_name.title())
    if cleaned and cleaned[0].isdigit():
        cleaned = "T_" + cleaned
    return cleaned or "Tool"


def _optional(py_type: Any) -> Any:
    """Widen a type to accept ``None``.

    ``NoneType`` and ``Any`` are already nullable; anything else gets a union.
    """
    if py_type is Any or py_type is None or py_type is NoneType:
        return py_type
    return py_type | None


def json_schema_to_pydantic(tool_name: str, schema: dict[str, Any]) -> type[BaseModel]:
    """Build the params model for a tool from its JSON Schema.

    Raises ``ValueError`` for a non-object schema, which is a caller bug (a
    malformed tool contract), not something a model should discover mid-run.
    """
    if not isinstance(schema, dict) or schema.get("type") not in (None, "object"):
        raise ValueError(
            f"Tool {tool_name!r}: parameters.type must be 'object' (got {schema.get('type')!r})"  # ty: ignore[possibly-missing-attribute]
        )
    properties: dict[str, Any] = schema.get("properties") or {}
    required: set[str] = set(schema.get("required") or [])

    fields: dict[str, Any] = {}
    for prop_name, prop_schema in properties.items():
        py_type = json_type_to_python(prop_schema)
        description = prop_schema.get("description") if isinstance(prop_schema, dict) else None
        extra: dict[str, Any] = {}
        if isinstance(prop_schema, dict):
            for key in _CONSTRAINT_KEYS:
                if key in prop_schema:
                    extra[key] = prop_schema[key]
        field_kwargs: dict[str, Any] = {"description": description}
        if extra:
            field_kwargs["json_schema_extra"] = extra
        if prop_name in required:
            fields[prop_name] = (py_type, Field(..., **field_kwargs))
        else:
            # An optional field must accept an explicit null, not merely be
            # absent. Models routinely send `null` for an unset optional --
            # and with MCP every schema comes from a third-party server, so
            # this is the common case rather than the rare one. A plain
            # `(str, Field(default=None))` rejects that with a ValidationError
            # the model cannot see or recover from.
            fields[prop_name] = (_optional(py_type), Field(default=None, **field_kwargs))

    if not fields:
        # An empty schema would be ambiguous; default to a single optional
        # ``input`` field so the LLM always has something concrete to send.
        fields["input"] = (str | None, Field(default=None, description="Optional input"))

    model_name = f"{safe_class_name(tool_name)}_Params"
    return create_model(model_name, **fields)  # ty: ignore[invalid-overload]


def normalize_object_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Coerce a tool input schema into the shape providers accept.

    MCP servers may omit ``type``, and some providers reject an object schema
    that has no ``properties``. Adding an empty mapping is harmless for a tool
    that genuinely takes no arguments and rescues one that would be rejected.
    """
    normalized = dict(schema)
    normalized.setdefault("type", "object")
    if normalized.get("properties") is None:
        normalized["properties"] = {}
    normalized.setdefault("required", [])
    return normalized


_INVALID_NAME_CHARS = re.compile(r"[^A-Za-z0-9_-]")

# Providers cap tool names at 64 characters.
MAX_TOOL_NAME_LENGTH = 64


def sanitize_tool_name(name: str) -> str:
    return _INVALID_NAME_CHARS.sub("_", name)


def truncate_tool_name(name: str, limit: int = MAX_TOOL_NAME_LENGTH) -> str:
    return name if len(name) <= limit else name[:limit]


__all__ = [
    "MAX_TOOL_NAME_LENGTH",
    "json_schema_to_pydantic",
    "json_type_to_python",
    "normalize_object_schema",
    "safe_class_name",
    "sanitize_tool_name",
    "truncate_tool_name",
]
