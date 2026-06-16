"""/model command - listing and switching models."""

from __future__ import annotations

import asyncio

from ...config import get_config
from ...llm import (
    DYNAMIC_PROVIDERS,
    Model,
    get_all_models,
    get_dynamic_provider,
    refresh_all_providers,
    refresh_provider,
)
from ..chat import ChatLog
from ..floating_list import ListItem
from ..selection_mode import SelectionMode
from ..widgets import InfoBar
from .base import CommandSupport


def _parse_hidden_entries(entries: list[str]) -> tuple[set[str], set[tuple[str, str]]]:
    """Split hidden-model entries into provider names and (provider, model) combos."""
    hidden_providers: set[str] = set()
    hidden_combos: set[tuple[str, str]] = set()
    for entry in entries:
        if ":" in entry:
            provider, _, model_id = entry.partition(":")
            provider, model_id = provider.strip(), model_id.strip()
            if provider and model_id:
                hidden_combos.add((provider, model_id))
        else:
            entry = entry.strip()
            if entry:
                hidden_providers.add(entry)
    return hidden_providers, hidden_combos


def _is_model_hidden(
    model: Model, hidden_providers: set[str], hidden_combos: set[tuple[str, str]]
) -> bool:
    return model.provider in hidden_providers or (model.provider, model.id) in hidden_combos


class ModelCommands(CommandSupport):
    def _handle_model_command(self, args: str) -> None:
        stripped = args.strip()
        if stripped:
            if stripped == "refresh":
                self.run_worker(self._refresh_dynamic_models(None), exclusive=False)
                return
            if stripped.startswith("refresh "):
                provider = stripped[len("refresh ") :].strip()
                self.run_worker(self._refresh_dynamic_models(provider or None), exclusive=False)
                return
            chat = self.query_one("#chat-log", ChatLog)
            chat.add_info_message(
                "Unknown /model sub-command. Use: /model, /model refresh, "
                "/model refresh <provider>",
                error=True,
            )
            return

        hidden_providers, hidden_combos = _parse_hidden_entries(get_config().ui.hidden_models)
        models = get_all_models()
        # Filter out hidden models, but always keep the currently active model
        # so its selection state is visible in the picker.
        if hidden_providers or hidden_combos:
            models = [
                m
                for m in models
                if not _is_model_hidden(m, hidden_providers, hidden_combos)
                or (m.id == self._runtime.model and m.provider == self._runtime.model_provider)
            ]
        # Apply the model_provider_filter. Empty string = show everything.
        filter_slug = get_config().ui.model_provider_filter
        if filter_slug:
            models = [m for m in models if m.provider == filter_slug]
        if not models:
            self.notify("No models configured", title="Models", timeout=3, severity="warning")
            return

        models.sort(key=lambda m: (m.provider, m.id))

        items: list[ListItem] = []
        for m in models:
            parts = [m.provider]
            if not m.supports_images:
                parts.append("[no-vision]")
            caption = " ".join(parts)
            label = (
                f"{m.id} ✓"
                if m.id == self._runtime.model and m.provider == self._runtime.model_provider
                else m.id
            )
            items.append(ListItem(value=m, label=label, description=caption))

        self._show_selection_picker(items, SelectionMode.MODEL)

    def _select_model(self, model) -> None:
        chat = self.query_one("#chat-log", ChatLog)
        info_bar = self.query_one("#info-bar", InfoBar)

        try:
            self._runtime.switch_model(model)
        except ValueError as e:
            chat.add_info_message(str(e), error=True)
            return
        self._sync_runtime_state()

        info_bar.set_model(model.id, model.provider)

        chat.add_info_message(f"Model changed to {model.id} ({model.provider})")

    async def _refresh_dynamic_models(self, provider: str | None) -> None:
        chat = self.query_one("#chat-log", ChatLog)

        if provider is not None and get_dynamic_provider(provider) is None:
            valid = ", ".join(sorted(DYNAMIC_PROVIDERS))
            chat.add_info_message(
                f"Unknown provider: {provider}. Dynamic providers: {valid}", error=True
            )
            return

        if provider is None:
            if not DYNAMIC_PROVIDERS:
                chat.add_info_message("No providers to refresh", error=True)
                return
            chat.add_info_message(f"Refreshing all {len(DYNAMIC_PROVIDERS)} providers...")
        else:
            chat.add_info_message(f"Refreshing {provider}...")

        def _run() -> dict[str, int | str]:
            if provider is not None:
                try:
                    count = refresh_provider(provider)
                except Exception as exc:
                    return {provider: -1, "_error": str(exc)}
                return {provider: count}
            return dict(refresh_all_providers())

        try:
            result = await asyncio.to_thread(_run)
        except Exception as exc:
            chat.add_info_message(f"Refresh failed: {exc}", error=True)
            return

        error = result.pop("_error", None)
        if error:
            chat.add_info_message(f"Refresh failed: {error}", error=True)
            return
        if not result:
            chat.add_info_message("Refresh complete (no providers returned models)")
            return
        lines = [f"  {name}: {count} models" for name, count in result.items()]
        chat.add_info_message("Refresh complete:\n" + "\n".join(lines))
