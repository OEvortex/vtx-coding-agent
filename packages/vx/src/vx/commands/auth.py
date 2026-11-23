"""``/login`` and ``/logout`` — provider authentication, ported from ctx-code.

Three kinds of login, same as ctx-code: OAuth flows, API-key entry, and keyless
local providers that only need their model list fetched.
"""

from __future__ import annotations

import asyncio
import contextlib
import webbrowser

from ctx.ai import (
    clear_api_key,
    clear_cline_credentials,
    clear_codex_credentials,
    clear_copilot_credentials,
    clear_deepai_credentials,
    cline_login,
    codex_login,
    copilot_login,
    get_dynamic_api_key,
    get_provider_info,
    get_provider_status,
    is_cline_logged_in,
    is_codex_logged_in,
    is_copilot_logged_in,
    is_deepai_logged_in,
    is_devin_logged_in,
    junie_login,
    list_providers,
    save_api_key,
)

from vx.commands.base import CommandSupport
from vx.tui import footer


def _status_label(provider: str) -> str:
    status = get_provider_status(provider)
    if status is None:
        return ""
    if status.has_env_key:
        return f"{status.env_var or 'env'} set"
    if status.has_stored_key:
        return "key stored"
    if status.api_key_optional:
        return "no key needed"
    return "key required"


def _oauth_providers() -> list[tuple[str, str, bool]]:
    """(id, label, already signed in) for each OAuth flow ctx ships."""
    return [
        ("github-copilot", "GitHub Copilot", is_copilot_logged_in()),
        ("codex", "OpenAI Codex", is_codex_logged_in()),
        ("cline", "Cline (WorkOS)", is_cline_logged_in()),
        ("devin", "Devin", is_devin_logged_in()),
        ("junie", "Junie (JetBrains)", False),
        ("deepai", "DeepAI", is_deepai_logged_in()),
    ]


class AuthCommands(CommandSupport):
    #: Provider whose key the next composer submission stores.
    _pending_api_key_provider: str | None = None

    # -- /login ----------------------------------------------------------

    def _handle_login_command(self, args: str) -> None:
        requested = args.strip()
        if requested:
            self._start_login_for(requested)
            return
        oauth = _oauth_providers()
        keyless = [p for p in list_providers() if p.api_key_optional]
        keyed = [p for p in list_providers() if not p.api_key_optional]
        items = [
            footer.MenuItem(
                label="OAuth",
                detail="browser login: " + ", ".join(name for _, name, _ in oauth),
                value="oauth",
            ),
            footer.MenuItem(
                label="API key",
                detail=f"paste a key for any of the {len(keyed)} providers",
                value="apikey",
            ),
            footer.MenuItem(
                label="Local / keyless",
                detail="no key needed: " + ", ".join(p.display_name for p in keyless),
                value="keyless",
            ),
        ]
        self._open_menu(items, noun="Sign in", on_select=self._on_login_method)

    def _start_login_for(self, provider_id: str) -> None:
        for oauth_id, _, _ in _oauth_providers():
            if oauth_id == provider_id:
                self._spawn(self._run_oauth(oauth_id))
                return
        info = get_provider_info(provider_id)
        if info is not None and info.api_key_optional:
            self._spawn(self._refresh_provider_models(provider_id))
            return
        if info is not None:
            self._prompt_for_api_key(provider_id)
            return
        self._fail(f"unknown provider: {provider_id}")

    def _on_login_method(self, item: footer.MenuItem) -> None:
        method = item.value or item.label
        if method == "oauth":
            oauth = _oauth_providers()
            self._open_menu(
                [
                    footer.MenuItem(
                        label=name,
                        detail="saved credentials" if saved else "oauth login",
                        metadata="signed in" if saved else "",
                        value=oauth_id,
                    )
                    for oauth_id, name, saved in oauth
                ],
                noun="OAuth providers",
                on_select=lambda picked: self._spawn(
                    self._run_oauth(picked.value or picked.label)
                ),
            )
            return
        if method == "apikey":
            providers = [p for p in list_providers() if not p.api_key_optional]
            self._open_menu(
                [
                    footer.MenuItem(
                        label=p.display_name,
                        detail=_status_label(p.slug),
                        metadata=p.slug,
                        value=p.slug,
                    )
                    for p in providers
                ],
                noun="Providers",
                on_select=lambda picked: self._prompt_for_api_key(picked.value or picked.label),
                filterable=True,
            )
            return
        keyless = [p for p in list_providers() if p.api_key_optional]
        self._open_menu(
            [
                footer.MenuItem(
                    label=p.display_name,
                    detail="local" if p.is_local else "no key needed",
                    metadata=p.slug,
                    value=p.slug,
                )
                for p in keyless
            ],
            noun="Keyless providers",
            on_select=lambda picked: self._spawn(
                self._refresh_provider_models(picked.value or picked.label)
            ),
        )

    def _prompt_for_api_key(self, provider_id: str) -> None:
        status = get_provider_status(provider_id)
        if status is None:
            self._fail(f"unknown provider: {provider_id}")
            return
        if get_dynamic_api_key(provider_id):
            self._open_menu(
                [
                    footer.MenuItem(
                        label="Update key", detail="paste a new API key", value="update"
                    ),
                    footer.MenuItem(
                        label="Clear key", detail="remove stored credentials", value="clear"
                    ),
                    footer.MenuItem(label="Cancel", detail="keep the current key", value="cancel"),
                ],
                noun=f"{provider_id} key",
                on_select=lambda item: self._on_api_key_action(
                    provider_id, item.value or item.label
                ),
            )
            return
        hint = f" (or set {status.env_var})" if status.env_var else ""
        self._notice(f"{provider_id} needs an API key{hint}", topic="login")
        self._prompt_input(
            f"Paste the {provider_id} API key{hint}:",
            lambda key: self._submit_api_key(provider_id, key),
            secret=True,
        )

    def _on_api_key_action(self, provider_id: str, action: str) -> None:
        if action == "clear":
            removed = clear_api_key(provider_id)
            self._ok(
                f"cleared the stored key for {provider_id}"
                if removed
                else f"no stored key for {provider_id}"
            )
            return
        if action == "cancel":
            self._notice(f"kept the existing key for {provider_id}", topic="login")
            return
        self._prompt_input(
            f"Paste the new {provider_id} API key:",
            lambda key: self._submit_api_key(provider_id, key),
            secret=True,
        )

    def _submit_api_key(self, provider_id: str, raw: str) -> None:
        key = raw.strip()
        if not key:
            self._notice("API key entry cancelled", topic="login")
            return
        try:
            if provider_id == "deepai":
                from ctx.ai import save_deepai_credentials
                from ctx.ai.oauth.deepai import DeepAICredentials, get_machine_id

                save_deepai_credentials(
                    DeepAICredentials(api_key=key, machine_id=get_machine_id())
                )
            else:
                save_api_key(provider_id, key)
        except ValueError as exc:
            self._fail(str(exc))
            return
        self._ok(f"saved the API key for {provider_id}", topic="login")
        self._spawn(self._refresh_provider_models(provider_id))

    async def _run_oauth(self, provider_id: str) -> None:
        flows = {
            "github-copilot": copilot_login,
            "codex": codex_login,
            "cline": cline_login,
            "junie": junie_login,
        }
        flow = flows.get(provider_id)
        if flow is None:
            self._warn(
                f"{provider_id} has no browser login here — use /login {provider_id} with a key"
            )
            return
        self._notice(f"starting {provider_id} sign-in…", topic="login")
        try:
            result = await asyncio.to_thread(flow)
        except Exception as exc:
            self._fail(f"{provider_id} sign-in failed: {exc}", topic="login")
            return
        url = _first_url(result)
        if url:
            self._notice(
                f"open this URL to authorize:\n{url}",
                topic="login",
                detail="the browser may have opened already",
            )
            with contextlib.suppress(Exception):
                await asyncio.to_thread(webbrowser.open, url)
        self._ok(f"{provider_id}: {_describe_login(result)}", topic="login")
        self._spawn(self._refresh_provider_models(provider_id))

    async def _refresh_provider_models(self, provider_id: str) -> None:
        from ctx.ai import DYNAMIC_PROVIDERS, refresh_provider
        from ctx.ai.model_fetcher import refresh_provider_models as refresh_legacy

        info = get_provider_info(provider_id)
        self._notice(f"fetching models for {provider_id}…", topic="login")

        def _run() -> int:
            count = 0
            if provider_id in DYNAMIC_PROVIDERS:
                with contextlib.suppress(Exception):
                    count = max(count, int(refresh_provider(provider_id)))
            if info is not None and info.fetch_models:
                with contextlib.suppress(Exception):
                    count = max(count, int(refresh_legacy(provider_id)))
            return count

        try:
            count = await asyncio.to_thread(_run)
        except Exception as exc:
            self._fail(f"{provider_id}: could not fetch models: {exc}", topic="login")
            return
        self._ok(f"{provider_id}: {count} models available", topic="login")

    # -- /logout ---------------------------------------------------------

    def _handle_logout_command(self, args: str) -> None:
        requested = args.strip()
        if requested:
            self._logout_provider(requested)
            return
        items: list[footer.MenuItem] = []
        if is_copilot_logged_in():
            items.append(footer.MenuItem(label="GitHub Copilot", value="github-copilot"))
        if is_codex_logged_in():
            items.append(footer.MenuItem(label="OpenAI Codex", value="codex"))
        if is_cline_logged_in():
            items.append(footer.MenuItem(label="Cline (WorkOS)", value="cline"))
        if is_devin_logged_in():
            items.append(footer.MenuItem(label="Devin", value="devin"))
        if is_deepai_logged_in():
            items.append(footer.MenuItem(label="DeepAI", value="deepai"))
        for provider in list_providers():
            status = get_provider_status(provider.slug)
            if status and status.has_stored_key:
                items.append(
                    footer.MenuItem(
                        label=provider.display_name,
                        detail="key stored",
                        metadata=provider.slug,
                        value=provider.slug,
                    )
                )
        if not items:
            self._notice("no providers are signed in", topic="logout")
            return
        self._open_menu(
            items,
            noun="Sign out",
            on_select=lambda item: self._logout_provider(item.value or item.label),
            filterable=True,
        )

    def _logout_provider(self, provider_id: str) -> None:
        clearers = {
            "github-copilot": (clear_copilot_credentials, "GitHub Copilot"),
            "codex": (clear_codex_credentials, "OpenAI Codex"),
            "cline": (clear_cline_credentials, "Cline"),
            "deepai": (clear_deepai_credentials, "DeepAI"),
        }
        clearer = clearers.get(provider_id)
        if clearer is not None:
            clearer[0]()
            self._ok(f"logged out of {clearer[1]}", topic="logout")
            return
        if get_provider_info(provider_id) is not None:
            removed = clear_api_key(provider_id)
            self._ok(
                f"cleared the stored key for {provider_id}"
                if removed
                else f"no stored key for {provider_id}",
                topic="logout",
            )
            return
        self._fail(f"unknown provider: {provider_id}")


def _first_url(result: object) -> str:
    """Find the authorization URL in whatever the oauth helper returns."""
    if isinstance(result, str) and result.startswith("http"):
        return result
    for attribute in ("authorization_url", "url", "verification_uri", "device_code_url"):
        value = getattr(result, attribute, None)
        if isinstance(value, str) and value.startswith("http"):
            return value
    if isinstance(result, dict):
        for key in ("authorization_url", "url", "verification_uri"):
            value = result.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
    return ""


def _describe_login(result: object) -> str:
    if result is None:
        return "sign-in finished"
    if isinstance(result, bool):
        return "signed in" if result else "sign-in failed"
    message = getattr(result, "message", None)
    return str(message) if message else "signed in"
