"""Lazy loader for the Devin/Windsurf ``exa.*`` protobuf descriptors.

The Devin CLI speaks Connect-RPC (``application/connect+proto``) to
``server.codeium.com`` using protobuf messages whose ``.proto`` sources are
not public. We vendor a ``FileDescriptorSet`` (extracted from the shipped
binaries, MIT-licensed via leookun/devin-2api) and build message classes
dynamically at runtime — no codegen step required.
"""

from __future__ import annotations

import contextlib
import threading
from importlib import resources
from typing import Any

_pool: Any = None
_message_index: dict[str, list[str]] = {}
_enum_index: dict[str, list[str]] = {}
_lock = threading.Lock()

# Preferred package order when resolving an unqualified name. The flattened
# devin-2api bundle prefixes types (``ExaChatPb_*``); the original descriptor
# set keeps them in their real packages.
_PACKAGE_HINTS = ("exa.api_server_pb", "exa.chat_pb", "exa.codeium_common_pb", "exa.cortex_pb")


def _load() -> None:
    global _pool, _message_index, _enum_index
    if _pool is not None:
        return
    with _lock:
        if _pool is not None:
            return
        from google.protobuf import descriptor_pb2, descriptor_pool

        # google.protobuf's generated modules are dynamically built; their
        # members aren't statically discoverable, so treat them as Any.
        pb2: Any = descriptor_pb2
        pool_mod: Any = descriptor_pool

        data = resources.files("vtx.ai.oauth.devin").joinpath("descriptors.pb").read_bytes()
        fds = pb2.FileDescriptorSet()
        fds.ParseFromString(data)

        pool = pool_mod.DescriptorPool()
        default = pool_mod.Default()
        added: set[str] = set()
        files_by_name = {f.name: f for f in fds.file}

        def add_file(file_proto: Any) -> None:
            if file_proto.name in added:
                return
            for dep in file_proto.dependency:
                if dep in added:
                    continue
                if dep in files_by_name:
                    add_file(files_by_name[dep])
                elif dep.startswith("google/protobuf/"):
                    try:
                        fdesc = default.FindFileByName(dep)
                    except KeyError:
                        continue
                    wkt = pb2.FileDescriptorProto()
                    fdesc.CopyToProto(wkt)
                    with contextlib.suppress(Exception):
                        pool.Add(wkt)
                    added.add(dep)
            with contextlib.suppress(Exception):
                pool.Add(file_proto)
            added.add(file_proto.name)

        for file_proto in fds.file:
            add_file(file_proto)

        messages: dict[str, list[str]] = {}
        enums: dict[str, list[str]] = {}

        def index_message(msg: Any, parent: str) -> None:
            full = f"{parent}.{msg.name}" if parent else msg.name
            short = msg.name
            messages.setdefault(short, []).append(full)
            for enum in msg.enum_type:
                enums.setdefault(enum.name, []).append(f"{full}.{enum.name}")
            for nested in msg.nested_type:
                index_message(nested, full)

        for file_proto in fds.file:
            pkg = file_proto.package
            for enum in file_proto.enum_type:
                enums.setdefault(enum.name, []).append(f"{pkg}.{enum.name}" if pkg else enum.name)
            for msg in file_proto.message_type:
                index_message(msg, pkg)

        _message_index = messages
        _enum_index = enums
        _pool = pool


def _resolve(name: str, kind: str) -> str:
    """Resolve a possibly-unqualified proto symbol name to a full name."""
    _load()
    index = _message_index if kind == "message" else _enum_index
    if "." in name:
        return name
    candidates = index.get(name, [])
    if not candidates:
        # Also accept flattened devin-2api style names (ExaChatPb_Foo).
        stripped = name
        for prefix in ("ExaApiServerPb_", "ExaChatPb_", "ExaCodeiumCommonPb_", "ExaCortexPb_"):
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix) :]
                break
        candidates = index.get(stripped, [])
        if not candidates:
            raise KeyError(f"Devin proto symbol not found: {name}")
    for pkg in _PACKAGE_HINTS:
        for candidate in candidates:
            if candidate.startswith(f"{pkg}."):
                return candidate
    return candidates[0]


def _find_descriptor_pool() -> Any:
    _load()
    return _pool


def message_class(name: str) -> type[Any]:
    """Return the protobuf message class for a message name.

    ``name`` may be fully qualified (``exa.chat_pb.ChatMessagePrompt``), a
    short name (``ChatMessagePrompt``), or a flattened devin-2api alias
    (``ExaChatPb_ChatMessagePrompt``); hinted packages are tried in order.
    """
    from google.protobuf import message_factory

    full = _resolve(name, "message")
    desc = _find_descriptor_pool().FindMessageTypeByName(full)
    return message_factory.GetMessageClass(desc)


def enum_descriptor(name: str) -> Any:
    """Return the EnumDescriptor for an enum name."""
    full = _resolve(name, "enum")
    return _find_descriptor_pool().FindEnumTypeByName(full)


def enum_value(enum_name: str, value_name: str) -> int:
    return enum_descriptor(enum_name).values_by_name[value_name].number


def clear_cache() -> None:
    """Test hook: drop loaded descriptors."""
    global _pool, _message_index, _enum_index
    _pool = None
    _message_index = {}
    _enum_index = {}


__all__ = ["clear_cache", "enum_descriptor", "enum_value", "message_class"]
