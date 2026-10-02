"""Tests for the GitHub App bot-identity module."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest import mock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from vtx.git import gh_app
from vtx.git.gh_app import (
    GitHubAppConfig,
    committer_env_vars,
    committer_identity,
    get_bot_user_id,
    get_installation_token,
    resolve_committer_vars,
)

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _gen_rsa_pem() -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


@pytest.fixture
def fake_pem(tmp_path: Path) -> str:
    p = tmp_path / "app_key.pem"
    p.write_bytes(_gen_rsa_pem())
    return str(p)


@pytest.fixture
def cfg(tmp_path: Path, fake_pem: str) -> GitHubAppConfig:
    return GitHubAppConfig(
        app_id="1234567",
        app_slug="vtx-coding-agent",
        pem_path=fake_pem,
        commit_as_bot=True,
        push_remote_owner="OEvortex",
        push_remote_repo="VTX",
    )


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate every test to a throwaway ~/.vtx/gh_app dir + fresh caches."""
    app_dir = tmp_path / "gh_app"
    app_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(gh_app, "_APP_DIR", app_dir)
    monkeypatch.setattr(gh_app, "_CONFIG_PATH", app_dir / "config.json")
    monkeypatch.setattr(gh_app, "_BOT_ID_CACHE", app_dir / "bot_user_id.json")
    gh_app._cached_token = ("", 0.0)
    gh_app._cached_bot_id = ("", 0)
    gh_app._cached_bot_id_at = 0.0
    yield app_dir
    gh_app._cached_token = ("", 0.0)
    gh_app._cached_bot_id = ("", 0)
    gh_app._cached_bot_id_at = 0.0


def _fake_get_installation():
    class R:
        status_code = 200

        def raise_for_status(self): ...
        def json(self):
            return {"id": 99}

    return R()


# ---------------------------------------------------------------------------
# config persistence
# ---------------------------------------------------------------------------


class TestConfig:
    def test_load_returns_none_when_missing(self, tmp_path, monkeypatch):
        # already isolated by autouse
        assert GitHubAppConfig.load() is None

    def test_save_then_load_roundtrip(self, fake_pem):
        cfg = GitHubAppConfig(app_id="111", app_slug="my-bot", pem_path=fake_pem)
        cfg.save()
        loaded = GitHubAppConfig.load()
        assert loaded is not None
        assert loaded.app_id == "111"
        assert loaded.app_slug == "my-bot"
        # config file perms are 0600
        assert oct((gh_app._CONFIG_PATH).stat().st_mode)[-3:] == "600"

    def test_commit_as_bot_false_yields_empty_env(self, fake_pem):
        cfg = GitHubAppConfig(app_id="1", app_slug="s", pem_path=fake_pem, commit_as_bot=False)
        cfg.save()
        assert resolve_committer_vars() == {}


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


class TestJwt:
    def test_make_jwt_has_three_parts(self, cfg):
        token = gh_app.make_jwt(cfg)
        assert token.count(".") == 2

    def test_jwt_payload_issuer_is_app_id(self, cfg):
        import jwt

        token = gh_app.make_jwt(cfg)
        payload = jwt.decode(token, options={"verify_signature": False})
        assert payload["iss"] == "1234567"
        assert payload["exp"] > payload["iat"]

    def test_jwt_expires_within_10_minutes(self, cfg):
        now = int(time.time())
        token = gh_app.make_jwt(cfg)
        import jwt

        payload = jwt.decode(token, options={"verify_signature": False})
        assert payload["exp"] <= now + 620
        assert payload["exp"] > now


# ---------------------------------------------------------------------------
# bot user id resolution + noreply email
# ---------------------------------------------------------------------------


class TestBotIdentity:
    def test_get_bot_user_id_uses_bot_user_not_app_id(self, cfg):
        """Critical: email must use the bot USER id, not the App ID."""
        seen_urls = []

        class FakeResp:
            status_code = 200

            def raise_for_status(self): ...
            def json(self):
                return {"id": 268339505}

        def fake_get(url, *a, **kw):
            seen_urls.append(url)
            return FakeResp()

        with mock.patch.object(gh_app.requests, "get", fake_get):
            bot_id = get_bot_user_id(cfg, jwt_token="x")
        assert bot_id == 268339505
        # targets /users/slug[bot] with URL-encoded brackets
        assert any("%5Bbot%5D" in u for u in seen_urls)

    def test_get_bot_user_id_cached_in_memory(self, cfg):
        seen = {"n": 0}

        class FakeResp:
            status_code = 200

            def raise_for_status(self): ...
            def json(self):
                seen["n"] += 1
                return {"id": 42}

        with mock.patch.object(gh_app.requests, "get", lambda *a, **kw: FakeResp()):
            first = get_bot_user_id(cfg, jwt_token="x")
            second = get_bot_user_id(cfg, jwt_token="x")
        assert first == 42 == second
        assert seen["n"] == 1  # second hit the in-memory cache

    def test_get_bot_user_id_disk_cache_persists(self, tmp_path, cfg):
        """Disk cache should be read when in-memory cache is cold."""

        # First call: populate disk cache
        class FakeResp:
            status_code = 200

            def raise_for_status(self): ...
            def json(self):
                return {"id": 99}

        with mock.patch.object(gh_app.requests, "get", lambda *a, **kw: FakeResp()):
            get_bot_user_id(cfg, jwt_token="x")
        assert (gh_app._BOT_ID_CACHE).exists()

        # Clear in-memory cache -> should read from disk
        gh_app._cached_bot_id = ("", 0)
        gh_app._cached_bot_id_at = 0.0

        calls = {"n": 0}

        class FakeResp2:
            status_code = 200

            def raise_for_status(self): ...
            def json(self):
                calls["n"] += 1
                return {"id": 99}

        with mock.patch.object(gh_app.requests, "get", lambda *a, **kw: FakeResp2()):
            assert get_bot_user_id(cfg, jwt_token="x") == 99
        assert calls["n"] == 0  # did NOT hit network

    def test_disk_cache_invalidates_on_app_id_change(self, tmp_path, fake_pem):
        """Disk cache for old app_id should be ignored."""
        # write stale cache for a different app
        (gh_app._BOT_ID_CACHE).write_text(
            json.dumps({"app_id": "old-app", "bot_user_id": 1, "cached_at": time.time()})
        )
        cfg = GitHubAppConfig(app_id="new-app", app_slug="vtx-coding-agent", pem_path=fake_pem)

        class FakeResp:
            status_code = 200

            def raise_for_status(self): ...
            def json(self):
                return {"id": 77}

        with mock.patch.object(gh_app.requests, "get", lambda *a, **kw: FakeResp()):
            assert get_bot_user_id(cfg, jwt_token="x") == 77

    def test_committer_identity_email_uses_bot_user_id(self, cfg):
        class FakeResp:
            status_code = 200

            def raise_for_status(self): ...
            def json(self):
                return {"id": 149130343}

        with mock.patch.object(gh_app.requests, "get", lambda *a, **kw: FakeResp()):
            name, email, bot_id = committer_identity(cfg)
        assert name == "vtx-coding-agent[bot]"
        assert bot_id == 149130343
        assert email == "149130343+vtx-coding-agent[bot]@users.noreply.github.com"
        assert "1234567" not in email

    def test_committer_env_vars_keys(self, cfg):
        class FakeResp:
            status_code = 200

            def raise_for_status(self): ...
            def json(self):
                return {"id": 5}

        with mock.patch.object(gh_app.requests, "get", lambda *a, **kw: FakeResp()):
            env = committer_env_vars(cfg)
        assert set(env) == {
            "GIT_AUTHOR_NAME",
            "GIT_AUTHOR_EMAIL",
            "GIT_COMMITTER_NAME",
            "GIT_COMMITTER_EMAIL",
        }
        assert env["GIT_AUTHOR_NAME"] == "vtx-coding-agent[bot]"
        assert env["GIT_AUTHOR_EMAIL"].endswith("@users.noreply.github.com")
        assert env["GIT_AUTHOR_EMAIL"] == env["GIT_COMMITTER_EMAIL"]
        assert env["GIT_AUTHOR_NAME"] == env["GIT_COMMITTER_NAME"]

    def test_committer_env_vars_fails_closed(self, cfg):
        """If identity resolution raises, return {} so bash never breaks."""

        def boom(*a, **kw):
            raise RuntimeError("network down")

        with mock.patch.object(gh_app.requests, "get", boom):
            assert committer_env_vars(cfg) == {}

    def test_resolve_committer_vars_no_config_is_empty(self):
        assert resolve_committer_vars() == {}


# ---------------------------------------------------------------------------
# installation token
# ---------------------------------------------------------------------------


class TestInstallationToken:
    def test_token_cached_and_reused(self, cfg):
        posts = {"n": 0}

        class FakeResp:
            status_code = 200

            def raise_for_status(self): ...
            def json(self):
                posts["n"] += 1
                return {"token": "ghs_tok", "expires_at": "2099-01-01T00:00:00Z"}

        with (
            mock.patch.object(gh_app.requests, "get", lambda *a, **kw: _fake_get_installation()),
            mock.patch.object(gh_app.requests, "post", lambda *a, **kw: FakeResp()),
        ):
            first = get_installation_token(cfg, "OEvortex", "VTX")
            second = get_installation_token(cfg, "OEvortex", "VTX")
        assert first == "ghs_tok" == second
        assert posts["n"] == 1

    def test_token_refreshed_past_expiry(self, cfg):
        posts = {"n": 0}

        class FakeResp:
            status_code = 200

            def raise_for_status(self): ...
            def json(self):
                posts["n"] += 1
                return {"token": f"tok-{posts['n']}"}

        with (
            mock.patch.object(gh_app.requests, "get", lambda *a, **kw: _fake_get_installation()),
            mock.patch.object(gh_app.requests, "post", lambda *a, **kw: FakeResp()),
        ):
            first = get_installation_token(cfg, "OEvortex", "VTX")
            gh_app._cached_token = (first, time.time() - 1)  # expired
            second = get_installation_token(cfg, "OEvortex", "VTX")
        assert first != second
        assert posts["n"] == 2


# ---------------------------------------------------------------------------
# integration: bash env includes bot identity when configured
# ---------------------------------------------------------------------------


class TestBashIntegration:
    def test_get_env_includes_bot_identity(self, cfg):
        cfg.save()
        # pre-populate caches so no network is needed in the bash hot path
        gh_app._cached_bot_id = (cfg.app_slug, 149130343)
        gh_app._cached_bot_id_at = time.time()

        from vtx.coding_agent.tools.bash import _get_env

        env = _get_env()
        assert env["GIT_AUTHOR_NAME"] == "vtx-coding-agent[bot]"
        assert (
            env["GIT_AUTHOR_EMAIL"] == "149130343+vtx-coding-agent[bot]@users.noreply.github.com"
        )

    def test_get_env_no_bot_identity_without_config(self):
        from vtx.coding_agent.tools.bash import _get_env

        env = _get_env()
        assert "GIT_AUTHOR_NAME" not in env
        assert "GIT_COMMITTER_EMAIL" not in env
