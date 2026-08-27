"""Tests for built-in OAuth providers (GitHub Copilot, Cline, OpenAI Codex OAuth)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import vtx.ai.oauth.cline as cline_oauth
import vtx.ai.oauth.codex as codex_oauth
import vtx.ai.oauth.copilot as copilot_oauth
import vtx.ai.oauth.dynamic as dynamic_oauth
from vtx.ai import (
    DYNAMIC_PROVIDERS,
    ProviderConfig,
    detect_provider_from_env,
    get_dynamic_api_key,
    get_provider_info,
    get_provider_status,
    get_valid_codex_token_sync,
    get_valid_copilot_token_sync,
    get_valid_openai_token_sync,
    list_providers,
)
from vtx.ai.provider_catalog import is_provider_configured
from vtx.ai.providers.openai_sdk import OpenAISDKProvider


@pytest.fixture(autouse=True)
def isolated_oauth_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(dynamic_oauth, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(copilot_oauth, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(cline_oauth, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(codex_oauth, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("vtx.core.paths.get_config_dir", lambda: tmp_path)
    # Clear ambient provider env vars so tests are fully deterministic
    for k in list(os.environ):
        if k.endswith("_API_KEY") or k.endswith("_TOKEN") or k.startswith("VTX_"):
            monkeypatch.delenv(k, raising=False)


def test_oauth_providers_in_catalog():
    providers = list_providers()
    slugs = {p.slug for p in providers}
    assert "github-copilot" in slugs
    assert "cline" in slugs
    assert "openai" in slugs

    copilot = get_provider_info("github-copilot")
    assert copilot is not None
    assert copilot.slug == "github-copilot"
    assert copilot.display_name == "GitHub Copilot"
    assert copilot.base_url == "https://api.individual.githubcopilot.com"
    assert "User-Agent" in copilot.headers

    cline = get_provider_info("cline")
    assert cline is not None
    assert cline.slug == "cline"
    assert cline.display_name == "Cline"
    assert cline.base_url == "https://api.cline.bot/v1"


def test_oauth_providers_in_dynamic_providers():
    assert "github-copilot" in DYNAMIC_PROVIDERS
    assert "cline" in DYNAMIC_PROVIDERS

    copilot_cfg = DYNAMIC_PROVIDERS["github-copilot"]
    assert copilot_cfg.base_url == "https://api.individual.githubcopilot.com"
    assert "User-Agent" in copilot_cfg.headers

    cline_cfg = DYNAMIC_PROVIDERS["cline"]
    assert cline_cfg.base_url == "https://api.cline.bot/v1"


def test_copilot_dynamic_api_key_and_status(tmp_path: Path):
    creds_file = tmp_path / "copilot_auth.json"
    copilot_token = "tid=123;exp=9999999999;proxy-ep=proxy.individual.githubcopilot.com"
    creds_file.write_text(
        json.dumps(
            {
                "github_token": "gh-oauth-123",
                "copilot_token": copilot_token,
                "expires_at": 9999999999000,
            }
        )
    )

    token = get_valid_copilot_token_sync()
    assert token == copilot_token

    dyn_token = get_dynamic_api_key("github-copilot")
    assert dyn_token == token

    status = get_provider_status("github-copilot")
    assert status is not None
    assert status.has_stored_key is True
    assert status.is_configured is True

    copilot_info = get_provider_info("github-copilot")
    assert copilot_info is not None
    assert is_provider_configured(copilot_info) is True


def test_cline_dynamic_api_key_and_status(tmp_path: Path):
    creds_file = tmp_path / "cline_auth.json"
    creds_file.write_text(
        json.dumps(
            {
                "access": "cline-jwt-access-token",
                "refresh": "cline-jwt-refresh-token",
                "expires": 9999999999000,
            }
        )
    )

    dyn_token = get_dynamic_api_key("cline")
    assert dyn_token == "cline-jwt-access-token"

    status = get_provider_status("cline")
    assert status is not None
    assert status.has_stored_key is True
    assert status.is_configured is True

    cline_info = get_provider_info("cline")
    assert cline_info is not None
    assert is_provider_configured(cline_info) is True


def test_codex_oauth_dynamic_api_key_and_status(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    creds_file = tmp_path / "codex_auth.json"
    creds_file.write_text(
        json.dumps(
            {
                "access": "codex-oauth-access-token",
                "refresh": "codex-oauth-refresh-token",
                "expires": 9999999999000,
                "account_id": "acc-123",
            }
        )
    )

    token = get_valid_codex_token_sync()
    assert token == "codex-oauth-access-token"
    assert get_valid_openai_token_sync() == "codex-oauth-access-token"

    dyn_token = get_dynamic_api_key("openai")
    assert dyn_token == "codex-oauth-access-token"

    status = get_provider_status("openai")
    assert status is not None
    assert status.has_stored_key is True
    assert status.is_configured is True


def test_detect_provider_from_env_with_copilot_oauth(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("VTX_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    creds_file = tmp_path / "copilot_auth.json"
    creds_file.write_text(
        json.dumps(
            {
                "github_token": "gh-oauth-123",
                "copilot_token": "tid=123;exp=9999999999",
                "expires_at": 9999999999000,
            }
        )
    )

    detected = detect_provider_from_env()
    assert detected.slug == "github-copilot"


def test_detect_provider_from_env_with_cline_oauth(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("VTX_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("CLINE_API_KEY", raising=False)

    creds_file = tmp_path / "cline_auth.json"
    creds_file.write_text(
        json.dumps(
            {"access": "cline-access", "refresh": "cline-refresh", "expires": 9999999999000}
        )
    )

    detected = detect_provider_from_env()
    assert detected.slug == "cline"


def test_openai_sdk_provider_with_copilot(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    creds_file = tmp_path / "copilot_auth.json"
    copilot_token = "tid=123;exp=9999999999;proxy-ep=proxy.individual.githubcopilot.com"
    creds_file.write_text(
        json.dumps(
            {
                "github_token": "gh-oauth-123",
                "copilot_token": copilot_token,
                "expires_at": 9999999999000,
            }
        )
    )

    config = ProviderConfig(
        model="gpt-4o",
        provider="github-copilot",
        base_url="https://api.individual.githubcopilot.com",
        default_headers={"User-Agent": "GitHubCopilotChat/0.35.0"},
    )
    provider = OpenAISDKProvider(config)
    assert provider._sdk.api_key == copilot_token
    assert provider._sdk.base_url == "https://api.individual.githubcopilot.com"
    assert provider._sdk._default_headers == {"User-Agent": "GitHubCopilotChat/0.35.0"}
