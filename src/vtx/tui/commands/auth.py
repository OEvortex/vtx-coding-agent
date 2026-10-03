"""/login and /logout commands - provider authentication flows.

There are three kinds of "logins" in vtx, each a sub-picker under /login:

- **OAuth flows** from ``src/vtx/ai/oauth`` (GitHub Copilot, OpenAI/Codex,
  Cline), where the user is sent to a browser to authorize and we store
  long-lived tokens.
- **API-key entries** for every keyed provider in
  ``src/vtx/ai/provider.yaml``. These don't need OAuth; the user pastes an
  API key and we store it in the key file (``~/vtx/dynamic_auth.yml``,
  plain text, mode 0600 - see :mod:`vtx.ai.oauth.dynamic` for the exact
  path resolution).
- **Keyless/local providers** (``api_key_optional: true`` in provider.yaml,
  e.g. Ollama) that need no credential at all - selecting one just fetches
  its model list.

Adding a new provider to ``provider.yaml`` automatically makes it appear in
the matching sub-picker.
"""

from __future__ import annotations

from vtx.ai import (
    clear_api_key,
    clear_cline_credentials,
    clear_codex_credentials,
    clear_copilot_credentials,
    cline_login,
    codex_login,
    copilot_login,
    get_copilot_token,
    get_dynamic_api_key,
    get_provider_info,
    get_provider_status,
    get_valid_cline_credentials,
    get_valid_codex_credentials,
    list_providers,
    save_api_key,
)
from vtx.ai import is_cline_logged_in as has_saved_cline_credentials
from vtx.ai import is_codex_logged_in as has_saved_codex_credentials
from vtx.ai import is_copilot_logged_in as has_saved_copilot_credentials
from vtx.tui.chat import ChatLog
from vtx.tui.commands.base import CommandSupport
from vtx.tui.floating_list import ListItem
from vtx.tui.input import InputBox
from vtx.tui.selection_mode import SelectionMode


def _status_label(provider: str) -> str:
    """Build the description shown next to a provider in the picker."""
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


def _oauth_login_providers() -> list[tuple[str, str, bool]]:
    """(provider_id, display_name, has_saved_credentials) per OAuth flow in src/vtx/ai/oauth."""
    return [
        ("github-copilot", "GitHub Copilot", has_saved_copilot_credentials()),
        ("codex", "OpenAI Codex", has_saved_codex_credentials()),
        ("cline", "Cline (WorkOS)", has_saved_cline_credentials()),
    ]


class AuthCommands(CommandSupport):
    def _handle_login_command(self, args: str) -> None:
        oauth = _oauth_login_providers()
        keyless = [p for p in list_providers() if p.api_key_optional]
        keyed_count = len(list_providers()) - len(keyless)
        items = [
            ListItem(
                value="oauth",
                label="OAuth",
                description="browser login: " + ", ".join(name for _, name, _ in oauth),
            ),
            ListItem(
                value="apikey",
                label="API Key",
                description=f"paste a key for any of the {keyed_count} providers",
            ),
            ListItem(
                value="keyless",
                label="Local / Keyless",
                description="no key needed: " + ", ".join(p.display_name for p in keyless),
            ),
        ]

        self._show_selection_picker(items, SelectionMode.LOGIN_METHOD)

    def _select_login_method(self, method: str) -> None:
        if method == "oauth":
            items = [
                ListItem(
                    value=provider_id,
                    label=name,
                    description="saved credentials" if has_oauth else "oauth login",
                )
                for provider_id, name, has_oauth in _oauth_login_providers()
            ]
            self._show_selection_picker(items, SelectionMode.LOGIN)
        elif method == "apikey":
            self._show_selection_picker(
                [
                    ListItem(value=p.slug, label=p.display_name, description=_status_label(p.slug))
                    for p in list_providers()
                    if not p.api_key_optional
                ],
                SelectionMode.LOGIN_API_KEY,
            )
        elif method == "keyless":
            self._show_selection_picker(
                [
                    ListItem(
                        value=p.slug,
                        label=p.display_name,
                        description="local" if p.is_local else "no key needed",
                    )
                    for p in list_providers()
                    if p.api_key_optional
                ],
                SelectionMode.LOGIN_KEYLESS,
            )

    def _select_login_provider(self, provider_id: str) -> None:
        if provider_id == "github-copilot":
            self.run_worker(self._copilot_login_flow(), exclusive=False)
            return

        if provider_id == "codex":
            self.run_worker(self._codex_login_flow(), exclusive=False)
            return

        if provider_id == "cline":
            self.run_worker(self._cline_login_flow(), exclusive=False)
            return

    def _select_apikey_provider(self, provider_id: str) -> None:
        if get_provider_info(provider_id) is not None:
            self._prompt_for_api_key(provider_id)

    def _select_keyless_provider(self, provider_id: str) -> None:
        """Keyless providers need no credential - just pull their model list."""
        chat = self.query_one("#chat-log", ChatLog)
        chat.add_info_message(f"Fetching models for {provider_id}...")
        self.run_worker(self._refresh_after_api_key(provider_id), exclusive=False)

    def _prompt_for_api_key(self, provider_id: str) -> None:
        """Put the input box in API_KEY mode so the next submission is treated
        as this provider's key (stored by _submit_api_key)."""
        status = get_provider_status(provider_id)
        chat = self.query_one("#chat-log", ChatLog)

        if status is None:
            chat.add_info_message(f"Unknown provider: {provider_id}", error=True)
            return

        env_var = status.env_var
        prompt_text = f"Enter API key for {provider_id}"
        if env_var:
            prompt_text += f" (or set {env_var})"
        prompt_text += ":"

        existing = get_dynamic_api_key(provider_id)
        if existing:
            # Already configured - allow update or clear.
            self._show_api_key_actions(provider_id)
            return

        chat.add_info_message(f"Provider {provider_id} needs an API key. {prompt_text}")

        input_box = self.query_one("#input-box", InputBox)
        input_box.set_placeholder(f"Paste {provider_id} API key (or /cancel)")
        self._selection_mode = SelectionMode.API_KEY
        self._pending_api_key_provider = provider_id
        input_box.focus()

    def _show_api_key_actions(self, provider_id: str) -> None:
        """For providers with a stored key, offer replace/clear."""
        items = [
            ListItem(value="update", label="Update key", description="paste a new API key"),
            ListItem(value="clear", label="Clear key", description="remove stored credentials"),
            ListItem(value="cancel", label="Cancel", description="keep current key"),
        ]
        self._pending_api_key_provider = provider_id
        self._show_selection_picker(items, SelectionMode.API_KEY_ACTION)

    def _select_api_key_action(self, action: str) -> None:
        provider_id = getattr(self, "_pending_api_key_provider", None)
        if not provider_id:
            return
        chat = self.query_one("#chat-log", ChatLog)

        if action == "clear":
            removed = clear_api_key(provider_id)
            if removed:
                chat.add_info_message(f"Cleared stored API key for {provider_id}")
            else:
                chat.add_info_message(f"No stored API key for {provider_id}")
            self._pending_api_key_provider = None
            return

        if action == "cancel":
            chat.add_info_message(f"Kept existing API key for {provider_id}")
            self._pending_api_key_provider = None
            return

        if action == "update":
            self._pending_api_key_provider = provider_id
            input_box = self.query_one("#input-box", InputBox)
            input_box.set_placeholder(f"Paste new {provider_id} API key (or /cancel)")
            self._selection_mode = SelectionMode.API_KEY
            input_box.focus()
            return

    def _submit_api_key(self, raw: str) -> None:
        """Called by the input layer when the user submits a key in API_KEY mode."""
        provider_id = getattr(self, "_pending_api_key_provider", None)
        chat = self.query_one("#chat-log", ChatLog)
        # Reset state immediately so a later error doesn't leave us stuck.
        self._pending_api_key_provider = None
        self._selection_mode = None

        # Restore the input box to its normal state.
        try:
            input_box = self.query_one("#input-box", InputBox)
            input_box.set_placeholder("")
            input_box.set_autocomplete_enabled(True)
            input_box.clear()
        except Exception:
            pass

        key = raw.strip()
        if not key or key.startswith("/"):
            chat.add_info_message("API key entry cancelled")
            return

        if not provider_id:
            chat.add_info_message("No provider selected for API key", error=True)
            return

        try:
            save_api_key(provider_id, key)
        except ValueError as exc:
            chat.add_info_message(str(exc), error=True)
            return

        chat.add_info_message(f"Saved API key for {provider_id}")
        self.run_worker(self._refresh_after_api_key(provider_id), exclusive=False)

    async def _refresh_after_api_key(self, provider_id: str) -> None:
        """Fetch a provider's model catalog; used after a key is saved and
        when a keyless provider is picked from /login."""
        import asyncio

        from vtx.ai import DYNAMIC_PROVIDERS, refresh_provider
        from vtx.ai.model_fetcher import refresh_provider_models as refresh_legacy
        from vtx.ai.provider_catalog import get as get_provider_info

        chat = self.query_one("#chat-log", ChatLog)

        info = get_provider_info(provider_id)
        uses_dynamic = provider_id in DYNAMIC_PROVIDERS
        uses_legacy = bool(info and info.fetch_models)

        if not uses_dynamic and not uses_legacy:
            chat.add_info_message("Use /model to pick a model for this provider.", error=False)
            return

        chat.add_info_message(f"Fetching models for {provider_id}...")

        def _run() -> int | str:
            best = 0
            last_err: str | None = None
            if uses_dynamic:
                try:
                    best = max(best, int(refresh_provider(provider_id)))
                except Exception as exc:
                    last_err = str(exc)
            if uses_legacy:
                try:
                    best = max(best, int(refresh_legacy(provider_id)))
                except Exception as exc:
                    last_err = str(exc)
            if best == 0 and last_err:
                return last_err
            return best

        try:
            result = await asyncio.to_thread(_run)
        except Exception as exc:
            chat.add_info_message(f"Model refresh failed: {exc}", error=True)
            return

        if isinstance(result, str):
            chat.add_info_message(f"Model refresh failed: {result}", error=True)
            return

        if result == 0:
            chat.add_info_message(
                f"Fetched models for {provider_id} (none returned). "
                "Use /model to check available models."
            )
        else:
            chat.add_info_message(
                f"Fetched {result} models for {provider_id}. Use /model to pick one."
            )

    async def _copilot_login_flow(self) -> None:
        import webbrowser

        chat = self.query_one("#chat-log", ChatLog)
        had_saved_credentials = has_saved_copilot_credentials()

        def on_user_code(url: str, code: str) -> None:
            webbrowser.open(url)
            self.call_later(
                chat.add_info_message,
                f"Opening browser to: {url}\n"
                f"Enter this code: {code}\n\n"
                "Waiting for authorization...",
            )

        try:
            if await get_copilot_token():
                chat.add_info_message("Already logged in to GitHub Copilot")
                return

            if had_saved_credentials:
                chat.add_info_message(
                    "Your saved GitHub Copilot session is no longer valid.", warning=True
                )
            else:
                chat.add_info_message("Starting GitHub Copilot login...")

            await copilot_login(on_user_code=on_user_code)
            chat.add_info_message(
                "Successfully logged in to GitHub Copilot!\n"
                "You can now use /model to select Copilot models."
            )
        except Exception as e:
            chat.add_info_message(f"Login failed: {e}", error=True)

    async def _codex_login_flow(self) -> None:
        import webbrowser

        chat = self.query_one("#chat-log", ChatLog)
        had_saved_credentials = has_saved_codex_credentials()

        def on_auth_url(url: str) -> None:
            webbrowser.open(url)
            self.call_later(
                chat.add_info_message,
                "Opening browser for OpenAI Codex OAuth...\n"
                f"If browser does not open, visit:\n{url}\n\n"
                "Waiting for authorization callback on http://localhost:1455/auth/callback ...",
            )

        try:
            if await get_valid_codex_credentials():
                chat.add_info_message("Already logged in to OpenAI Codex")
                return

            if had_saved_credentials:
                chat.add_info_message("Your saved Codex session is no longer valid.", warning=True)
            else:
                chat.add_info_message("Starting OpenAI Codex login...")

            await codex_login(on_auth_url=on_auth_url)
            chat.add_info_message(
                "Successfully logged in to OpenAI Codex!\n"
                "You can now use /model to select Codex models."
            )
        except Exception as e:
            chat.add_info_message(f"Login failed: {e}", error=True)

    async def _cline_login_flow(self) -> None:
        import webbrowser

        chat = self.query_one("#chat-log", ChatLog)
        had_saved = has_saved_cline_credentials()

        def on_user_code(url: str, code: str) -> None:
            webbrowser.open(url)
            self.call_later(
                chat.add_info_message,
                f"Opening browser to: {url}\n"
                f"Enter this code: {code}\n\n"
                "Waiting for authorization...",
            )

        try:
            if await get_valid_cline_credentials():
                chat.add_info_message("Already logged in to Cline")
                return

            if had_saved:
                chat.add_info_message("Your saved Cline session is no longer valid.", warning=True)
            else:
                chat.add_info_message("Starting Cline login (WorkOS device flow)...")

            # Auto-reuse native Cline CLI token if present
            from vtx.ai.oauth.cline import load_cline_credentials

            native = load_cline_credentials()
            if native and not had_saved:
                chat.add_info_message(
                    "Found existing Cline CLI token, using it. Use /logout to clear."
                )
                return

            await cline_login(on_user_code=on_user_code)
            chat.add_info_message(
                "Successfully logged in to Cline!\nYou can now use /model to select cline models."
            )
        except Exception as e:
            chat.add_info_message(f"Login failed: {e}", error=True)

    def _handle_logout_command(self, args: str) -> None:
        items: list[ListItem] = []

        if has_saved_copilot_credentials():
            items.append(ListItem(value="github-copilot", label="GitHub Copilot", description=""))
        if has_saved_codex_credentials():
            items.append(ListItem(value="codex", label="OpenAI Codex", description=""))
        if has_saved_cline_credentials():
            items.append(ListItem(value="cline", label="Cline (WorkOS)", description=""))

        for p in list_providers():
            status = get_provider_status(p.slug)
            if status and status.has_stored_key:
                items.append(
                    ListItem(value=p.slug, label=p.display_name, description="key stored")
                )

        if not items:
            chat = self.query_one("#chat-log", ChatLog)
            chat.add_info_message("No providers logged in")
            return

        self._show_selection_picker(items, SelectionMode.LOGOUT)

    def _select_logout_provider(self, provider_id: str) -> None:
        chat = self.query_one("#chat-log", ChatLog)

        if provider_id == "github-copilot":
            clear_copilot_credentials()
            chat.add_info_message("Logged out of GitHub Copilot")
            return

        if provider_id == "codex":
            clear_codex_credentials()
            chat.add_info_message("Logged out of OpenAI Codex")
            return

        if provider_id == "cline":
            clear_cline_credentials()
            chat.add_info_message("Logged out of Cline")
            return

        if get_provider_info(provider_id) is not None:
            if clear_api_key(provider_id):
                chat.add_info_message(f"Cleared stored API key for {provider_id}")
            else:
                chat.add_info_message(f"No stored API key for {provider_id}")
            return
