"""Stateful OAuth credential storage for one MCP server.

:class:`McpOAuthProvider` is the default implementation of the flow's provider
protocol. It keeps everything the flow needs to resume -- tokens, the PKCE
verifier, the CSRF state, cached discovery -- behind a pluggable
:class:`OAuthStateStore`, so durability is the caller's choice.

Two properties are deliberate:

* Writes are serialized. The flow saves the client information and then the
  tokens, and an interleaved read-modify-write would lose one of them.
* State belonging to a different server URL is ignored. A single store file
  holds every server's credentials, and reading another server's would hand its
  token to this one.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import tempfile
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from vtx.core.paths import get_config_dir

from .errors import McpOAuthAuthorizationRequiredError  # noqa: F401  (re-export convenience)
from .types import (
    OAuthClientInformation,
    OAuthClientMetadata,
    OAuthDiscoveryState,
    OAuthTokens,
    parse_authorization_server_metadata,
    parse_protected_resource_metadata,
)

log = logging.getLogger("mcp.oauth.provider")

CREDENTIALS_FILENAME = "mcp-auth.json"

# invalidate_credentials kinds
INVALIDATE_ALL = "all"
INVALIDATE_CLIENT = "client"
INVALIDATE_TOKENS = "tokens"
INVALIDATE_VERIFIER = "verifier"
INVALIDATE_DISCOVERY = "discovery"


@dataclass
class McpOAuthState:
    """Everything stored for one server. Serialized as-is."""

    server_url: str
    client_information: dict[str, Any] | None = None
    tokens: dict[str, Any] | None = None
    # Epoch seconds, derived from ``expires_in`` at the time of saving.
    tokens_expire_at: float | None = None
    code_verifier: str | None = None
    oauth_state: str | None = None
    discovery: dict[str, Any] | None = None


class OAuthStateStore(Protocol):
    async def load(self) -> McpOAuthState | None: ...

    async def save(self, state: McpOAuthState) -> None: ...


class MemoryOAuthStateStore:
    """In-process storage. Credentials do not survive a restart."""

    def __init__(self) -> None:
        self._value: McpOAuthState | None = None

    async def load(self) -> McpOAuthState | None:
        if self._value is None:
            return None
        return McpOAuthState(**asdict(self._value))

    async def save(self, state: McpOAuthState) -> None:
        self._value = McpOAuthState(**asdict(state))


class FileOAuthStateStore:
    """One JSON file holding every server's credentials.

    Mode 0600 and an atomic replace, because this file holds bearer tokens and
    a partially-written one would leave the user unable to authenticate at all.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (get_config_dir() / CREDENTIALS_FILENAME)
        self._lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        return self._path

    async def load(self) -> McpOAuthState | None:
        async with self._lock:
            try:
                raw = self._path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return None
            except OSError as exc:
                log.warning("Could not read MCP credentials at %s: %s", self._path, exc)
                return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # A corrupt file is not worth crashing a session over; re-auth is
            # always possible and this just means doing it sooner.
            log.warning("Discarding corrupt MCP credentials at %s", self._path)
            return None
        servers = data.get("servers")
        if not isinstance(servers, dict):
            return None
        # A flat {server_url: state} map is read as well as the nested form, so
        # a file written by an earlier shape still works.
        entries = servers.values() if isinstance(servers, dict) else []
        for entry in entries:
            if isinstance(entry, dict) and "server_url" in entry:
                return McpOAuthState(**entry)
        return None

    async def save(self, state: McpOAuthState) -> None:
        async with self._lock:
            data: dict[str, Any] = {}
            if self._path.exists():
                with contextlib.suppress(OSError, json.JSONDecodeError):
                    loaded = json.loads(self._path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        data = loaded
            servers = data.get("servers")
            if not isinstance(servers, dict):
                servers = {}
            servers[state.server_url] = asdict(state)
            data["servers"] = servers
            self._write(data)

    def _write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp_name = tempfile.mkstemp(
            prefix=f"{self._path.name}.", suffix=".tmp", dir=self._path.parent
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp.chmod(0o600)
            os.replace(tmp, self._path)
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise


def _encode_discovery(state: OAuthDiscoveryState) -> dict[str, Any]:
    return {
        "authorization_server_url": state.authorization_server_url,
        "authorization_server_metadata": (
            asdict(state.authorization_server_metadata)
            if state.authorization_server_metadata
            else None
        ),
        "resource_metadata": (
            asdict(state.resource_metadata) if state.resource_metadata else None
        ),
        "resource_metadata_url": state.resource_metadata_url,
    }


def _decode_discovery(raw: dict[str, Any] | None) -> OAuthDiscoveryState | None:
    if not isinstance(raw, dict) or not raw.get("authorization_server_url"):
        return None
    metadata = raw.get("authorization_server_metadata")
    resource = raw.get("resource_metadata")
    return OAuthDiscoveryState(
        authorization_server_url=raw["authorization_server_url"],
        authorization_server_metadata=(
            _rebuild(parse_authorization_server_metadata, metadata) if metadata else None
        ),
        resource_metadata=(
            _rebuild(parse_protected_resource_metadata, resource) if resource else None
        ),
        resource_metadata_url=raw.get("resource_metadata_url"),
    )


def _rebuild(parser: Any, raw: dict[str, Any]) -> Any:
    try:
        return parser(raw)
    except ValueError:
        # A metadata document that no longer validates is re-fetched rather
        # than being allowed to fail the whole flow.
        log.debug("Discarding stored OAuth metadata that no longer validates")
        return None


@dataclass
class McpOAuthProvider:
    """The default stateful provider for one exact MCP server URL."""

    server_url: str
    redirect_url: str
    client_metadata: OAuthClientMetadata
    store: OAuthStateStore = field(default_factory=MemoryOAuthStateStore)
    on_redirect: Any = None
    """``async (url: str) -> None``. Where the browser is opened, or printed."""

    def __post_init__(self) -> None:
        self.server_url = _normalize_url(self.server_url)
        # Serializes the read-modify-write cycles the flow performs.
        self._writes: asyncio.Lock = asyncio.Lock()

    # ---- provider protocol ------------------------------------------------

    async def state(self) -> str:
        """The CSRF ``state`` value, generated once and reused across attempts.

        Reusing it means a second sign-in attempt supersedes the first, which is
        what we want: an abandoned browser tab cannot later complete a flow
        started after it.
        """
        existing = (await self._load()).oauth_state
        if existing:
            return existing
        value = secrets.token_hex(16)
        await self._update(lambda s: replace(s, oauth_state=value))
        return value

    async def client_information(self) -> OAuthClientInformation | None:
        raw = (await self._load()).client_information
        return OAuthClientInformation.from_dict(raw) if raw else None

    async def save_client_information(self, information: OAuthClientInformation) -> None:
        await self._update(lambda s: replace(s, client_information=information.to_dict()))

    async def tokens(self) -> OAuthTokens | None:
        raw = (await self._load()).tokens
        return OAuthTokens.from_dict(raw) if raw else None

    async def save_tokens(self, tokens: OAuthTokens) -> None:
        expires_at = time.time() + tokens.expires_in if tokens.expires_in is not None else None

        await self._update(
            lambda s: replace(s, tokens=tokens.to_dict(), tokens_expire_at=expires_at)
        )

    async def redirect_to_authorization(self, url: str) -> None:
        if self.on_redirect is None:
            raise RuntimeError(
                "No redirect handler: the OAuth flow needs somewhere to send the user"
            )
        result = self.on_redirect(url)
        if asyncio.iscoroutine(result):
            await result

    async def save_code_verifier(self, verifier: str) -> None:
        await self._update(lambda s: replace(s, code_verifier=verifier))

    async def code_verifier(self) -> str:
        verifier = (await self._load()).code_verifier
        if not verifier:
            raise ValueError("No OAuth PKCE code verifier is stored")
        return verifier

    async def invalidate_credentials(self, kind: str) -> None:
        def apply(state: McpOAuthState) -> McpOAuthState:
            everything = kind == INVALIDATE_ALL
            return replace(
                state,
                client_information=(
                    None if (everything or kind == INVALIDATE_CLIENT) else state.client_information
                ),
                tokens=None if (everything or kind == INVALIDATE_TOKENS) else state.tokens,
                tokens_expire_at=(
                    None if (everything or kind == INVALIDATE_TOKENS) else state.tokens_expire_at
                ),
                code_verifier=(
                    None if (everything or kind == INVALIDATE_VERIFIER) else state.code_verifier
                ),
                discovery=None
                if (everything or kind == INVALIDATE_DISCOVERY)
                else state.discovery,
                oauth_state=None if everything else state.oauth_state,
            )

        await self._update(apply)

    async def save_discovery_state(self, state: OAuthDiscoveryState) -> None:
        await self._update(lambda s: replace(s, discovery=_encode_discovery(state)))

    async def discovery_state(self) -> OAuthDiscoveryState | None:
        return _decode_discovery((await self._load()).discovery)

    # ---- storage ----------------------------------------------------------

    async def _load(self) -> McpOAuthState:
        async with self._writes:
            stored = await self.store.load()
        return self._own(stored)

    async def _update(self, apply: Any) -> None:
        async with self._writes:
            stored = await self.store.load()
            await self.store.save(apply(self._own(stored)))

    def _own(self, state: McpOAuthState | None) -> McpOAuthState:
        """Discard state stored for a different server.

        Without this a single credentials file would happily hand one server's
        bearer token to another, which is a real credential leak, not a tidiness
        concern.
        """
        if state is not None and state.server_url == self.server_url:
            return state
        return McpOAuthState(server_url=self.server_url)


def _normalize_url(value: str) -> str:
    from urllib.parse import urlparse, urlunparse

    parsed = urlparse(value)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", "", ""))


__all__ = [
    "CREDENTIALS_FILENAME",
    "FileOAuthStateStore",
    "McpOAuthProvider",
    "McpOAuthState",
    "MemoryOAuthStateStore",
    "OAuthStateStore",
]
