import json
from pathlib import Path

import pytest

from vtx.mcp.config import (
    add_mcp_server_config,
    load_mcp_config,
    remove_mcp_server_config,
    update_mcp_server_config,
    validate_mcp_server_config,
    write_mcp_config,
)


def _write(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def test_reads_a_stdio_server():
    config, error = validate_mcp_server_config(
        "fs", {"command": "npx", "args": ["-y", "server-filesystem", "."], "env": {"A": "b"}}
    )
    assert error is None
    assert config is not None
    assert config.is_stdio
    assert config.command == "npx"
    assert config.args == ["-y", "server-filesystem", "."]
    assert config.enabled is True
    assert config.timeout_seconds == 60


def test_reads_an_http_server():
    config, error = validate_mcp_server_config(
        "docs",
        {
            "url": "https://example.com/mcp",
            "headers": {"Authorization": "Bearer x"},
            "oauth": {"clientId": "abc", "scope": "read"},
            "timeout": 30,
        },
    )
    assert error is None
    assert config is not None
    assert not config.is_stdio
    assert config.url == "https://example.com/mcp"
    assert config.timeout_seconds == 30
    assert config.oauth is not None
    assert config.oauth.client_id == "abc"


@pytest.mark.parametrize(
    "name,value,expected",
    [
        ("bad name", {"command": "x"}, "must match"),
        ("a", {"command": "x", "url": "https://x"}, "exactly one"),
        ("a", {}, "exactly one"),
        ("a", {"command": ""}, "non-empty"),
        ("a", {"command": "x", "args": [1]}, "args must be"),
        ("a", {"command": "x", "env": {"A": 1}}, "env must be"),
        ("a", {"command": "x", "enabled": "yes"}, "enabled must be"),
        ("a", {"command": "x", "timeout": 0}, "timeout must be"),
        ("a", {"url": "ftp://x"}, "http(s) URL"),
        ("a", {"url": "https://x", "headers": {"A": 1}}, "headers must be"),
        ("a", {"url": "https://x", "oauth": "nope"}, "oauth must be"),
        ("a", {"url": "https://x", "oauth": {"callbackPort": 0}}, "callbackPort must be"),
        ("a", {"command": "x", "url": "https://x", "headers": {}}, "exactly one"),
    ],
)
def test_rejects_malformed_entries(name, value, expected):
    config, error = validate_mcp_server_config(name, value)
    assert config is None
    assert error is not None and expected in error


def test_rejects_stdio_servers_carrying_http_keys():
    config, error = validate_mcp_server_config("a", {"command": "x", "headers": {"A": "b"}})
    assert config is None
    assert error is not None and "stdio servers take no" in error


def test_loads_global_then_project_with_project_winning(tmp_path: Path):
    config_dir = tmp_path / "config"
    project = tmp_path / "project"
    _write(
        config_dir / "mcp.json",
        {"mcpServers": {"a": {"command": "global-a"}, "b": {"command": "global-b"}}},
    )
    _write(project / ".vtx" / "mcp.json", {"mcpServers": {"a": {"command": "project-a"}}})

    # Untrusted: the project file is not read at all.
    untrusted = load_mcp_config(cwd=str(project), project_trusted=False, config_dir=config_dir)
    assert {s.name: s.command for s in untrusted.servers} == {"a": "global-a", "b": "global-b"}
    assert all(s.scope == "global" for s in untrusted.servers)

    trusted = load_mcp_config(cwd=str(project), project_trusted=True, config_dir=config_dir)
    assert {s.name: s.command for s in trusted.servers} == {"a": "project-a", "b": "global-b"}
    by_name = {s.name: s for s in trusted.servers}
    assert by_name["a"].scope == "project"
    assert by_name["b"].scope == "global"


def test_a_disabled_server_is_loaded_but_not_enabled(tmp_path: Path):
    _write(tmp_path / "mcp.json", {"mcpServers": {"a": {"command": "x", "enabled": False}}})
    config = load_mcp_config(cwd=str(tmp_path), config_dir=tmp_path)
    assert config.get("a") is not None
    assert config.get("a").enabled is False
    assert config.enabled_servers == []


def test_reports_a_broken_file_without_raising(tmp_path: Path):
    (tmp_path / "mcp.json").write_text("{not json", encoding="utf-8")
    config = load_mcp_config(cwd=str(tmp_path), config_dir=tmp_path)
    assert config.servers == []
    assert len(config.errors) == 1


def test_reports_a_bad_entry_and_keeps_the_good_one(tmp_path: Path):
    _write(
        tmp_path / "mcp.json", {"mcpServers": {"good": {"command": "x"}, "bad": {"url": "nope"}}}
    )
    config = load_mcp_config(cwd=str(tmp_path), config_dir=tmp_path)
    assert [s.name for s in config.servers] == ["good"]
    assert len(config.errors) == 1


def test_missing_files_are_not_errors(tmp_path: Path):
    config = load_mcp_config(cwd=str(tmp_path), config_dir=tmp_path / "absent")
    assert config.servers == []
    assert config.errors == []


def test_env_expansion(monkeypatch):
    monkeypatch.setenv("DOCS_TOKEN", "secret")
    monkeypatch.setenv("EMPTY", "")
    config, _ = validate_mcp_server_config(
        "a", {"url": "https://x", "headers": {"Authorization": "Bearer ${DOCS_TOKEN}"}}
    )
    assert config is not None
    assert config.resolved_headers() == {"Authorization": "Bearer secret"}

    # ${NAME} and $NAME both expand; an unset variable expands to empty rather
    # than raising, so a missing optional token does not stop a server starting.
    stdio, _ = validate_mcp_server_config(
        "b", {"command": "x", "env": {"A": "$EMPTY", "B": "${UNSET}"}}
    )
    assert stdio is not None
    assert stdio.resolved_env() == {"A": "", "B": ""}


def test_update_preserves_other_content_and_indentation(tmp_path: Path):
    path = _write(
        tmp_path / "mcp.json",
        {"autoEnable": True, "mcpServers": {"a": {"command": "x", "enabled": False}}},
    )
    update_mcp_server_config(path, "a", {"enabled": True})
    parsed = json.loads(path.read_text())
    assert parsed["autoEnable"] is True
    # Re-enabling removes the key rather than writing an explicit default.
    assert "enabled" not in parsed["mcpServers"]["a"]


def test_update_rejects_an_unknown_server(tmp_path: Path):
    path = _write(tmp_path / "mcp.json", {"mcpServers": {"a": {"command": "x"}}})
    with pytest.raises(ValueError, match="does not define"):
        update_mcp_server_config(path, "missing", {"enabled": False})


def test_add_and_remove(tmp_path: Path):
    path = tmp_path / "mcp.json"
    config, _ = validate_mcp_server_config("a", {"command": "x", "args": ["1"]})
    assert config is not None
    add_mcp_server_config(path, "a", config)
    assert json.loads(path.read_text())["mcpServers"]["a"] == {"command": "x", "args": ["1"]}

    assert remove_mcp_server_config(path, "a") is True
    assert json.loads(path.read_text())["mcpServers"] == {}
    assert remove_mcp_server_config(path, "a") is False


def test_write_matches_existing_indentation(tmp_path: Path):
    path = tmp_path / "mcp.json"
    path.write_text('{\n    "mcpServers": {}\n}\n', encoding="utf-8")
    write_mcp_config(path, {"mcpServers": {"a": {"command": "x"}}})
    text = path.read_text()
    assert '\n    "mcpServers"' in text
    assert text.endswith("\n")


def test_add_does_not_emit_empty_optional_keys(tmp_path: Path):
    path = tmp_path / "mcp.json"
    config, _ = validate_mcp_server_config("a", {"url": "https://x/mcp"})
    assert config is not None
    add_mcp_server_config(path, "a", config)
    entry = json.loads(path.read_text())["mcpServers"]["a"]
    assert entry == {"url": "https://x/mcp"}
