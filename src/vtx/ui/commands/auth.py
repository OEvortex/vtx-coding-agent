"""/login and /logout commands - provider authentication flows.

There are two kinds of "logins" in vtx:

- **OAuth flows** for GitHub Copilot and OpenAI Codex, where the user is sent
  to a browser to authorize and we store long-lived tokens.
- **API-key entries** for every provider in ``src/vtx/llm/provider.yaml``.
  These don't need OAuth; the user pastes an API key and we store it in
  ``~/.vtx/dynamic_auth.json`` (mode 0600).

Both kinds show up together in the ``/login`` picker so the user has a single
place to manage credentials. Adding a new provider to ``provider.yaml``
automatically makes it appear in the picker.
"""

from __future__ import annotations

from ...llm import (
    clear_api_key,
    clear_copilot_credentials,
    clear_openai_credentials,
    copilot_login,
    get_copilot_token,
    get_dynamic_api_key,
    get_provider_info,
    get_provider_status,
    get_valid_openai_credentials,
    list_providers,
    openai_login,
    save_api_key,
)
from ...llm import is_copilot_logged_in as has_saved_copilot_credentials
from ...llm import is_openai_logged_in as has_saved_openai_credentials
from ..chat import ChatLog
from ..floating_list import ListItem
from ..input import InputBox
from ..selection_mode import SelectionMode
from .base import CommandSupport


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


class AuthCommands(CommandSupport):
    def _handle_login_command(self, args: str) -> None:
        providers: list[tuple[str, str, bool, str]] = [
            ("github-copilot", "GitHub Copilot", has_saved_copilot_credentials(), "oauth"),
            ("openai", "OpenAI (ChatGPT/Codex)", has_saved_openai_credentials(), "oauth"),
        ]

        # Every provider in provider.yaml gets a key-based entry.
        # Providers that also have an OAuth entry above get a suffixed value
        # so both flows are reachable from the picker.
        oauth_slugs = {pid for pid, _, _, kind in providers if kind == "oauth"}
        for p in list_providers():
            value = f"{p.slug}-key" if p.slug in oauth_slugs else p.slug
            providers.append((value, p.display_name, False, "key"))

        items: list[ListItem] = []
        for provider_id, name, has_oauth, kind in providers:
            if kind == "oauth":
                description = "saved credentials" if has_oauth else "oauth login"
            else:
                slug = (
                    provider_id.removesuffix("-key")
                    if provider_id.endswith("-key")
                    else provider_id
                )
                description = _status_label(slug)
            items.append(ListItem(value=provider_id, label=name, description=description))

        self._show_selection_picker(items, SelectionMode.LOGIN)

    def _select_login_provider(self, provider_id: str) -> None:
        if provider_id == "github-copilot":
            self.run_worker(self._copilot_login_flow(), exclusive=False)
            return

        if provider_id == "openai":
            self.run_worker(self._openai_login_flow(), exclusive=False)
            return

        # Key-based entries for OAuth-capable providers are suffixed (e.g.
        # "openai-key") so both flows are reachable. Strip the suffix to get
        # the real provider slug.
        if provider_id.endswith("-key"):
            provider_id = provider_id.removesuffix("-key")

        if get_provider_info(provider_id) is not None:
            self._prompt_for_api_key(provider_id)
            return

    def _prompt_for_api_key(self, provider_id: str) -> None:
        """Show a single-line input that captures the API key and stores it."""
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
        from ..input import InputBox

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
        """Refresh the model catalog for a provider after its API key was saved."""
        import asyncio

        from ...llm import DYNAMIC_PROVIDERS, refresh_provider

        chat = self.query_one("#chat-log", ChatLog)

        if provider_id not in DYNAMIC_PROVIDERS:
            chat.add_info_message("Use /model to pick a model for this provider.", error=False)
            return

        chat.add_info_message(f"Fetching models for {provider_id}...")

        def _run() -> int | str:
            try:
                return refresh_provider(provider_id)
            except Exception as exc:
                return str(exc)

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

    async def _openai_login_flow(self) -> None:
        import webbrowser

        chat = self.query_one("#chat-log", ChatLog)
        had_saved_credentials = has_saved_openai_credentials()

        def on_auth_url(url: str) -> None:
            webbrowser.open(url)
            self.call_later(
                chat.add_info_message,
                "Opening browser for OpenAI OAuth...\n"
                f"If browser does not open, visit:\n{url}\n\n"
                "Waiting for authorization callback on http://localhost:1455/auth/callback ...",
            )

        try:
            if await get_valid_openai_credentials():
                chat.add_info_message("Already logged in to OpenAI")
                return

            if had_saved_credentials:
                chat.add_info_message(
                    "Your saved OpenAI session is no longer valid.", warning=True
                )
            else:
                chat.add_info_message("Starting OpenAI login...")

            await openai_login(on_auth_url=on_auth_url)
            chat.add_info_message(
                "Successfully logged in to OpenAI!\n"
                "You can now use /model to select openai-codex models."
            )
        except Exception as e:
            chat.add_info_message(f"Login failed: {e}", error=True)

    def _handle_logout_command(self, args: str) -> None:
        items: list[ListItem] = []

        if has_saved_copilot_credentials():
            items.append(ListItem(value="github-copilot", label="GitHub Copilot", description=""))
        if has_saved_openai_credentials():
            items.append(ListItem(value="openai", label="OpenAI (ChatGPT/Codex)", description=""))

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

        if provider_id == "openai":
            clear_openai_credentials()
            chat.add_info_message("Logged out of OpenAI")
            return

        if get_provider_info(provider_id) is not None:
            if clear_api_key(provider_id):
                chat.add_info_message(f"Cleared stored API key for {provider_id}")
            else:
                chat.add_info_message(f"No stored API key for {provider_id}")
            return
