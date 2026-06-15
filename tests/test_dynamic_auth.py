"""Tests for the dynamic-provider API-key auth flow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vtx.llm.base import ProviderConfig
from vtx.llm.oauth.dynamic import (
    clear_api_key,
    get_dynamic_api_key,
    get_dynamic_auth_path,
    get_provider_status,
    has_api_key,
    load_api_key,
    save_api_key,
)
from vtx.llm.providers.openai_sdk import OpenAISDKProvider


@pytest.fixture(autouse=True)
def isolated_auth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Redirect get_config_dir to a temp dir so each test has a clean auth file."""
    import vtx.llm.oauth.dynamic as dynamic_oauth

    monkeypatch.setattr(dynamic_oauth, "get_config_dir", lambda: tmp_path)


# =================================================================================================
# File IO
# =================================================================================================


def test_get_dynamic_auth_path_under_config_dir(tmp_path: Path):
    assert get_dynamic_auth_path() == tmp_path / "dynamic_auth.json"


def test_save_and_load_api_key(tmp_path: Path):
    save_api_key("kilo", "test-key-123")
    loaded = load_api_key("kilo")
    assert loaded == "test-key-123"

    data = json.loads((tmp_path / "dynamic_auth.json").read_text())
    assert data["kilo"] == "test-key-123"


def test_has_api_key(tmp_path: Path):
    assert has_api_key("kilo") is False
    save_api_key("kilo", "key")
    assert has_api_key("kilo") is True


def test_clear_api_key(tmp_path: Path):
    save_api_key("kilo", "key")
    clear_api_key("kilo")
    assert has_api_key("kilo") is False


def test_get_provider_status(tmp_path: Path):
    status = get_provider_status("tokenrouter")
    assert status is not None
    assert status.is_configured is False

    save_api_key("tokenrouter", "key")
    status = get_provider_status("tokenrouter")
    assert status is not None
    assert status.is_configured is True


def test_get_dynamic_api_key(tmp_path: Path):
    assert get_dynamic_api_key("kilo") is None
    save_api_key("kilo", "key")
    assert get_dynamic_api_key("kilo") == "key"


# =================================================================================================
# Provider integration
# =================================================================================================


def test_openai_sdk_provider_reads_dynamic_key(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    save_api_key("airouter", "dynamic-key-123")

    config = ProviderConfig(
        model="test-model", provider="airouter", base_url="https://api.airouter.in/v1"
    )
    provider = OpenAISDKProvider(config)
    assert provider._sdk.api_key == "dynamic-key-123"
