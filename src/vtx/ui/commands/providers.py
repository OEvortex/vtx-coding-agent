"""/provider command - dropdown picker for scoping the /model picker to one provider."""

from __future__ import annotations

from ...config import get_config, set_model_provider_filter
from ...llm import list_providers
from ..chat import ChatLog
from ..floating_list import ListItem
from ..selection_mode import SelectionMode
from .base import CommandSupport

_ALL_SLUG = "__all__"


class ProviderCommands(CommandSupport):
    def _handle_provider_command(self, args: str) -> None:
        """Open the provider picker that scopes /model to a single provider.

        The first row "All providers" clears the filter so /model shows every
        provider again. Picking any other row sets the filter to that single
        provider. ``/provider reset`` is also accepted as a shortcut for the
        "All providers" row.
        """
        chat = self.query_one("#chat-log", ChatLog)
        providers = list_providers()
        if not providers:
            chat.add_info_message("No providers configured", error=True)
            return

        if args.strip().lower() in ("reset", "all", "clear"):
            set_model_provider_filter("")
            chat.show_status("Provider filter cleared - /model now shows all providers")
            return

        self._show_providers_picker()

    def _show_providers_picker(self) -> None:
        items = self._build_providers_items()
        self._show_selection_picker(items, SelectionMode.PROVIDER, max_label_width=40)

    def _build_providers_items(self) -> list[ListItem[str]]:
        active = get_config().ui.model_provider_filter
        items: list[ListItem[str]] = []

        all_label = "All providers ✓" if not active else "All providers"
        all_desc = (
            "currently showing every provider in /model"
            if not active
            else "show every provider in /model"
        )
        items.append(ListItem(value=_ALL_SLUG, label=all_label, description=all_desc))

        for p in list_providers():
            enabled = active and p.slug == active
            label = f"{p.slug} ✓" if enabled else p.slug
            if enabled:
                description = f"{p.display_name} - filter active, /model shows only this"
            else:
                description = p.display_name
            items.append(ListItem(value=p.slug, label=label, description=description))
        return items

    def _select_provider_set(self, value: str) -> None:
        chat = self.query_one("#chat-log", ChatLog)
        if value == _ALL_SLUG:
            set_model_provider_filter("")
            chat.show_status("Provider filter cleared - /model now shows all providers")
            return

        set_model_provider_filter(value)
        chat.show_status(f"Provider set to {value} - /model will show only {value} models")
