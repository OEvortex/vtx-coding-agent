"""GitHub App authentication + bot commit identity for the Vtx coding agent.

A GitHub App (not a plain machine-user account) is what gives Vtx commits the
``vtx-coding-agent[bot]`` attribution that ``claude[bot]`` / ``dependabot[bot]``
use. GitHub generates the app's RSA private key (PEM) for you when the app is
created via the manifest flow — it is never generated here.

Flow:
  1. Create the app via ``.agents/skills/vtx-gh-app/`s SKILL.md` (one browser click).
  2. `GitHubAppConfig` holds the App ID + PEM path + slug (written by the skill).
  3. At commit time the bash tool injects ``GIT_AUTHOR_*`` / ``GIT_COMMITTER_*``
     env vars pointing at the bot identity, so every Vtx commit is attributed to
     ``vtx-coding-agent[bot]`` without touching the user's personal git config.
  4. Push auth (1h installation token) is wired per-repo by ``configure_repo``.

Two non-obvious facts the implementation bakes in (both flagged in the
referenced dev-to / josh-ops write-ups):
  * the noreply email uses the **bot user's** numeric ID, NOT the App ID;
  * the bot user (``slug[bot]``) only exists after the app is **installed** on a repo.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import jwt
import requests

from vtx.core.paths import get_config_dir

_APP_DIR = get_config_dir() / "gh_app"
_CONFIG_PATH = _APP_DIR / "config.json"
_BOT_ID_CACHE = _APP_DIR / "bot_user_id.json"

# Installation tokens live 1h; refresh a bit early to avoid edge expiry.
_TOKEN_TTL_SECONDS = 55 * 60
# Bot user IDs are stable (they only change if the app is recreated); cache on
# disk keyed by App ID so we don't hit the API on every process start.
_BOT_ID_CACHE_TTL = 24 * 3600

_GITHUB_API = "https://api.github.com"


@dataclass
class GitHubAppConfig:
    """Persistent configuration for the Vtx GitHub App identity."""

    app_id: str
    app_slug: str
    pem_path: str
    commit_as_bot: bool = True
    # owner/repo the app's installation token should target; set by configure_repo.
    push_remote_owner: str = ""
    push_remote_repo: str = ""

    @classmethod
    def load(cls, path: Path | None = None) -> GitHubAppConfig | None:
        p = path or _CONFIG_PATH
        if not p.exists():
            return None
        data = json.loads(p.read_text())
        return cls(
            app_id=str(data["app_id"]),
            app_slug=data["app_slug"],
            pem_path=data["pem_path"],
            commit_as_bot=data.get("commit_as_bot", True),
            push_remote_owner=data.get("push_remote_owner", ""),
            push_remote_repo=data.get("push_remote_repo", ""),
        )

    def save(self, path: Path | None = None) -> None:
        p = path or _CONFIG_PATH
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "app_id": self.app_id,
            "app_slug": self.app_slug,
            "pem_path": self.pem_path,
            "commit_as_bot": self.commit_as_bot,
            "push_remote_owner": self.push_remote_owner,
            "push_remote_repo": self.push_remote_repo,
        }
        p.write_text(json.dumps(data, indent=2))
        os.chmod(p, 0o600)

    @property
    def pem(self) -> str:
        return Path(self.pem_path).read_text()


def is_configured() -> bool:
    """True if a GitHub App config exists and looks usable."""
    cfg = GitHubAppConfig.load()
    if cfg is None:
        return False
    try:
        Path(cfg.pem_path).read_text()
    except OSError:
        return False
    return True


def make_jwt(cfg: GitHubAppConfig) -> str:
    """Sign a short-lived (10 min) JWT with the App's private key."""
    now = int(time.time())
    payload = {"iat": now - 30, "exp": now + 600, "iss": cfg.app_id}
    return jwt.encode(payload, cfg.pem, algorithm="RS256")


def _headers(jwt_or_token: str, as_app: bool = True) -> dict[str, str]:
    return {
        "Authorization": (f"Bearer {jwt_or_token}") if as_app else f"token {jwt_or_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


# (token, expires_at) in-memory cache across the process lifetime.
_cached_token: tuple[str, float] = ("", 0.0)


def get_installation_token(cfg: GitHubAppConfig, owner: str, repo: str) -> str:
    """Mint (or reuse a still-valid) installation token for owner/repo.

    Tokens live up to 1h. We cache for the process lifetime and refresh ~5 min
    before expiry to avoid hitting the API on every push.
    """
    global _cached_token
    token, expires_at = _cached_token
    if token and time.time() < expires_at:
        return token

    jwt_token = make_jwt(cfg)
    installation_id = _resolve_installation_id(cfg, jwt_token, owner, repo)
    r = requests.post(
        f"{_GITHUB_API}/app/installations/{installation_id}/access_tokens",
        headers=_headers(jwt_token),
        json={"repository": f"{owner}/{repo}"},
        timeout=5,
    )
    r.raise_for_status()
    body = r.json()
    token = body["token"]
    raw_expiry = body.get("expires_at")
    if raw_expiry:
        # GitHub returns ISO-8601, e.g. "2026-09-12T00:00:00Z".
        expires_at = time.strptime(raw_expiry, "%Y-%m-%dT%H:%M:%SZ")
        expires_at = time.mktime(expires_at)
    else:
        expires_at = time.time() + _TOKEN_TTL_SECONDS
    # Refresh 5 min before real expiry so we never hand out a dead token.
    _cached_token = (token, expires_at - 300)
    return token


def _resolve_installation_id(cfg: GitHubAppConfig, jwt_token: str, owner: str, repo: str) -> int:
    """Find the installation ID for this app on owner/repo."""
    r = requests.get(
        f"{_GITHUB_API}/repos/{owner}/{repo}/installation", headers=_headers(jwt_token), timeout=5
    )
    if r.status_code == 200:
        return int(r.json()["id"])
    # Fallback: list installations and match by slug + repo.
    r = requests.get(f"{_GITHUB_API}/app/installations", headers=_headers(jwt_token), timeout=5)
    r.raise_for_status()
    for inst in r.json():
        if inst.get("app_slug") != cfg.app_slug:
            continue
        if f"{owner}/{repo}" in {
            f"{r['owner']}/{r['name']}" for r in inst.get("repositories", [])
        }:
            return int(inst["id"])
    raise RuntimeError(
        f"App '{cfg.app_slug}' is not installed on {owner}/{repo}. "
        "Install it via the app settings page."
    )


_cached_bot_id: tuple[str, int] = ("", 0)
_cached_bot_id_at = 0.0


def _load_bot_id_from_disk(cfg: GitHubAppConfig) -> tuple[int, float] | None:
    if not _BOT_ID_CACHE.exists():
        return None
    try:
        data = json.loads(_BOT_ID_CACHE.read_text())
        if data.get("app_id") != cfg.app_id:
            return None
        cached_at = float(data.get("cached_at", 0))
        if time.time() - cached_at > _BOT_ID_CACHE_TTL:
            return None
        return int(data["bot_user_id"]), cached_at
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None


def _save_bot_id_to_disk(cfg: GitHubAppConfig, bot_id: int) -> None:
    try:
        _BOT_ID_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _BOT_ID_CACHE.write_text(
            json.dumps({"app_id": cfg.app_id, "bot_user_id": bot_id, "cached_at": time.time()})
        )
    except OSError:
        pass


def get_bot_user_id(cfg: GitHubAppConfig, jwt_token: str | None = None) -> int:
    """Resolve the bot user's numeric ID from ``GET /users/<slug>[bot]``.

    This is the **bot user's** ID (the ``slug[bot]`` machine account), NOT the
    App ID. Using the App ID in the noreply commit email yields an unverified
    commit without the ``[bot]`` badge — a very common mistake.
    """
    global _cached_bot_id, _cached_bot_id_at
    slug, bot_id = _cached_bot_id
    now = time.time()
    if slug == cfg.app_slug and _cached_bot_id_at and now - _cached_bot_id_at < 3600:
        return bot_id

    # Try disk cache (persists across process restarts)
    disk = _load_bot_id_from_disk(cfg)
    if disk is not None:
        bot_id, cached_at = disk
        _cached_bot_id = (cfg.app_slug, bot_id)
        _cached_bot_id_at = cached_at
        return bot_id

    if jwt_token is None:
        jwt_token = make_jwt(cfg)
    encoded = f"{cfg.app_slug}%5Bbot%5D"  # URL-encode [ and ]
    r = requests.get(f"{_GITHUB_API}/users/{encoded}", headers=_headers(jwt_token), timeout=5)
    r.raise_for_status()
    bot_id = int(r.json()["id"])
    _cached_bot_id = (cfg.app_slug, bot_id)
    _cached_bot_id_at = time.time()
    _save_bot_id_to_disk(cfg, bot_id)
    return bot_id


def committer_identity(cfg: GitHubAppConfig) -> tuple[str, str, int]:
    """Return ``(name, email, bot_user_id)`` for ``git config``.

    name  = ``<slug>[bot]``
    email = ``<bot_user_id>+<slug>[bot]@users.noreply.github.com``
    """
    jwt_token = make_jwt(cfg)
    bot_id = get_bot_user_id(cfg, jwt_token)
    name = f"{cfg.app_slug}[bot]"
    email = f"{bot_id}+{cfg.app_slug}[bot]@users.noreply.github.com"
    return name, email, bot_id


def committer_env_vars(cfg: GitHubAppConfig) -> dict[str, str]:
    """Env vars to inject into bash so git commits are attributed to the bot.

    Returned keys: ``GIT_AUTHOR_NAME``, ``GIT_AUTHOR_EMAIL``,
    ``GIT_COMMITTER_NAME``, ``GIT_COMMITTER_EMAIL``. Empty dict on failure.
    """
    if not cfg.commit_as_bot:
        return {}
    try:
        name, email, _ = committer_identity(cfg)
    except Exception:
        # Never let identity resolution block a bash command. Fall back to the
        # user's existing git config instead of failing.
        return {}
    return {
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
    }


def resolve_committer_vars() -> dict[str, str]:
    """Public entry point used by the bash tool's environment builder.

    Cheap fast-path: returns cached env vars (or ``{}``) without network on
    cache hits. Only does an API call on the first run in a process (or when the
    disk cache is cold/stale), and fails closed (returns ``{}``) on any error.
    """
    cfg = GitHubAppConfig.load()
    if cfg is None or not cfg.commit_as_bot:
        return {}
    return committer_env_vars(cfg)
