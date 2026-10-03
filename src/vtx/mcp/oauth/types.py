"""OAuth metadata and token shapes, with dependency-free structural validation.

The models are plain dataclasses built by hand-validating the parsed JSON rather
than deserialized by a validation library. Metadata documents carry fields vtx
does not use, and dropping them would mean re-fetching on the next run; so each
parser copies the document through and overwrites only the fields it checked.

Adapted from modelcontextprotocol/typescript-sdk v1.29.0 (MIT).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from .errors import OAuthError

# Schemes that must never appear in a URL we fetched from, or will hand to a
# browser: each executes code rather than describing a resource.
_DANGEROUS_SCHEMES = frozenset({"javascript:", "data:", "vbscript:"})


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"Invalid {name}")
    return value


def _required_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Invalid {name}")
    return value


def _optional_string(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, name)


def _optional_strings(value: Any, name: str) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Invalid {name}")
    return [item for item in value if isinstance(item, str)]


def safe_url(value: Any, name: str) -> str:
    text = _required_string(value, name)
    scheme = urlparse(text).scheme.lower()
    if scheme in _DANGEROUS_SCHEMES:
        raise ValueError(f"Invalid {name}")
    return text


def _optional_url(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return safe_url(value, name)


@dataclass
class OAuthProtectedResourceMetadata:
    """RFC 9728: what a protected MCP server says about its authorization."""

    resource: str
    authorization_servers: list[str] = field(default_factory=list)
    scopes_supported: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuthorizationServerMetadata:
    """RFC 8414 / OpenID Connect Discovery."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None = None
    scopes_supported: list[str] = field(default_factory=list)
    response_types_supported: list[str] = field(default_factory=list)
    grant_types_supported: list[str] = field(default_factory=list)
    token_endpoint_auth_methods_supported: list[str] = field(default_factory=list)
    code_challenge_methods_supported: list[str] = field(default_factory=list)
    client_id_metadata_document_supported: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class OAuthTokens:
    access_token: str
    token_type: str
    expires_in: int | None = None
    scope: str | None = None
    refresh_token: str | None = None
    id_token: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            k: v
            for k, v in {
                "access_token": self.access_token,
                "token_type": self.token_type,
                "expires_in": self.expires_in,
                "scope": self.scope,
                "refresh_token": self.refresh_token,
                "id_token": self.id_token,
            }.items()
            if v is not None
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OAuthTokens:
        return cls(
            access_token=_required_string(data.get("access_token"), "access_token"),
            token_type=_required_string(data.get("token_type"), "token_type"),
            expires_in=data.get("expires_in"),
            scope=_optional_string(data.get("scope"), "scope"),
            refresh_token=_optional_string(data.get("refresh_token"), "refresh_token"),
            id_token=_optional_string(data.get("id_token"), "id_token"),
        )


@dataclass
class OAuthClientMetadata:
    """What we tell the authorization server about ourselves at registration."""

    redirect_uris: list[str]
    client_name: str | None = None
    client_uri: str | None = None
    logo_uri: str | None = None
    scope: str | None = None
    contacts: list[str] = field(default_factory=list)
    grant_types: list[str] = field(default_factory=list)
    response_types: list[str] = field(default_factory=list)
    token_endpoint_auth_method: str | None = None
    software_id: str | None = None
    software_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"redirect_uris": list(self.redirect_uris)}
        for key, value in {
            "client_name": self.client_name,
            "client_uri": self.client_uri,
            "logo_uri": self.logo_uri,
            "scope": self.scope,
            "grant_types": list(self.grant_types) or None,
            "response_types": list(self.response_types) or None,
            "token_endpoint_auth_method": self.token_endpoint_auth_method,
            "software_id": self.software_id,
            "software_version": self.software_version,
        }.items():
            if value:
                out[key] = value
        if self.contacts:
            out["contacts"] = list(self.contacts)
        return out


@dataclass
class OAuthClientInformation:
    """What the authorization server told us about our registration."""

    client_id: str
    client_secret: str | None = None
    client_id_issued_at: int | None = None
    client_secret_expires_at: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            k: v
            for k, v in {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "client_id_issued_at": self.client_id_issued_at,
                "client_secret_expires_at": self.client_secret_expires_at,
                **self.extra,
            }.items()
            if v is not None
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OAuthClientInformation:
        input = _object(data, "OAuth client registration response")
        known = {"client_id", "client_secret", "client_id_issued_at", "client_secret_expires_at"}
        return cls(
            client_id=_required_string(input.get("client_id"), "client_id"),
            client_secret=_optional_string(input.get("client_secret"), "client_secret"),
            client_id_issued_at=input.get("client_id_issued_at"),
            client_secret_expires_at=input.get("client_secret_expires_at"),
            extra={k: v for k, v in input.items() if k not in known},
        )


@dataclass
class OAuthChallenge:
    """Parsed ``WWW-Authenticate`` from a 401 or an insufficient-scope 403."""

    resource_metadata_url: str | None = None
    scope: str | None = None
    error: str | None = None
    error_description: str | None = None


@dataclass
class OAuthDiscoveryState:
    """Cached discovery, so a reconnect does not re-fetch metadata."""

    authorization_server_url: str
    authorization_server_metadata: AuthorizationServerMetadata | None = None
    resource_metadata: OAuthProtectedResourceMetadata | None = None
    resource_metadata_url: str | None = None


@dataclass
class OAuthServerInfo:
    authorization_server_url: str
    authorization_server_metadata: AuthorizationServerMetadata | None = None
    resource_metadata: OAuthProtectedResourceMetadata | None = None


def parse_protected_resource_metadata(value: Any) -> OAuthProtectedResourceMetadata:
    input = _object(value, "OAuth protected resource metadata")
    return OAuthProtectedResourceMetadata(
        resource=safe_url(input.get("resource"), "OAuth protected resource metadata resource"),
        authorization_servers=[
            safe_url(url, "authorization server URL")
            for url in (
                _optional_strings(input.get("authorization_servers"), "authorization_servers")
                or []
            )
        ],
        scopes_supported=_optional_strings(input.get("scopes_supported"), "scopes_supported")
        or [],
        raw=dict(input),
    )


def parse_authorization_server_metadata(value: Any) -> AuthorizationServerMetadata:
    input = _object(value, "authorization server metadata")
    response_types = _optional_strings(
        input.get("response_types_supported"), "response_types_supported"
    )
    if response_types is None:
        raise ValueError("Invalid response_types_supported")
    document_supported = input.get("client_id_metadata_document_supported")
    return AuthorizationServerMetadata(
        issuer=safe_url(input.get("issuer"), "authorization server issuer"),
        authorization_endpoint=safe_url(
            input.get("authorization_endpoint"), "authorization endpoint"
        ),
        token_endpoint=safe_url(input.get("token_endpoint"), "token endpoint"),
        registration_endpoint=_optional_url(
            input.get("registration_endpoint"), "registration endpoint"
        ),
        scopes_supported=_optional_strings(input.get("scopes_supported"), "scopes_supported")
        or [],
        response_types_supported=response_types,
        grant_types_supported=_optional_strings(
            input.get("grant_types_supported"), "grant_types_supported"
        )
        or [],
        token_endpoint_auth_methods_supported=_optional_strings(
            input.get("token_endpoint_auth_methods_supported"),
            "token_endpoint_auth_methods_supported",
        )
        or [],
        code_challenge_methods_supported=_optional_strings(
            input.get("code_challenge_methods_supported"), "code_challenge_methods_supported"
        )
        or [],
        client_id_metadata_document_supported=(
            document_supported if isinstance(document_supported, bool) else False
        ),
        raw=dict(input),
    )


def parse_oauth_tokens(value: Any) -> OAuthTokens:
    input = _object(value, "OAuth token response")
    expires_in = input.get("expires_in")
    if expires_in is not None:
        try:
            expires_in = int(expires_in)
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid expires_in") from exc
    return OAuthTokens(
        access_token=_required_string(input.get("access_token"), "access_token"),
        token_type=_required_string(input.get("token_type"), "token_type"),
        expires_in=expires_in,
        scope=_optional_string(input.get("scope"), "scope"),
        refresh_token=_optional_string(input.get("refresh_token"), "refresh_token"),
        id_token=_optional_string(input.get("id_token"), "id_token"),
    )


def parse_oauth_error(status: int, body: Any) -> OAuthError:
    """Build an :class:`OAuthError` from a token endpoint's response.

    Servers report OAuth errors with any status, sometimes 200, so the body is
    the authority and the status is only a fallback.
    """
    if isinstance(body, dict) and isinstance(body.get("error"), str):
        return OAuthError(
            body["error"],
            body.get("error_description") or body["error"],
            body.get("error_uri") if isinstance(body.get("error_uri"), str) else None,
        )
    return OAuthError("server_error", f"HTTP {status}")


__all__ = [
    "AuthorizationServerMetadata",
    "OAuthChallenge",
    "OAuthClientInformation",
    "OAuthClientMetadata",
    "OAuthDiscoveryState",
    "OAuthProtectedResourceMetadata",
    "OAuthServerInfo",
    "OAuthTokens",
    "parse_authorization_server_metadata",
    "parse_oauth_error",
    "parse_oauth_tokens",
    "parse_protected_resource_metadata",
    "safe_url",
]
