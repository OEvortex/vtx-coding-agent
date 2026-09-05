"""/model command - listing and switching models."""

from __future__ import annotations

import asyncio

from vtx.ai import (
    DYNAMIC_PROVIDERS,
    Model,
    get_all_models,
    get_dynamic_provider,
    refresh_all_providers,
    refresh_provider,
)
from vtx.ai.config import get_config, get_recent_models
from vtx.tui.chat import ChatLog
from vtx.tui.commands.base import CommandSupport
from vtx.tui.floating_list import ListItem
from vtx.tui.selection_mode import SelectionMode
from vtx.tui.widgets import InfoBar


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

        # Cache-only read - never block on network here. Any exception is
        # surfaced as a chat message so a broken install (fresh `pip install`
        # with empty ~/.vtx/models/) does not appear as a silent no-op.
        # This is the user-visible symptom when installed via `uv tool`/`pip`
        # with a `model_provider_filter` pointing at a provider whose
        # `known_models` is empty and no cache has been fetched yet - the
        # picker would otherwise show "No models configured" with no guidance.
        try:
            hidden_providers, hidden_combos = _parse_hidden_entries(get_config().ui.hidden_models)
            all_models = get_all_models()
        except Exception as exc:
            chat = self.query_one("#chat-log", ChatLog)
            chat.add_info_message(f"Failed to load model catalog: {exc}", error=True)
            return
        # Filter out hidden models, but always keep the currently active model
        # so its selection state is visible in the picker.
        if hidden_providers or hidden_combos:
            all_models = [
                m
                for m in all_models
                if not _is_model_hidden(m, hidden_providers, hidden_combos)
                or (m.id == self._runtime.model and m.provider == self._runtime.model_provider)
            ]
        if not all_models:
            chat = self.query_one("#chat-log", ChatLog)
            # No cached models at all - fresh pip/uv-tool install or corrupted cache.
            # Guide the user to refresh instead of a bare toast.
            chat.add_info_message(
                "No models configured — cache is empty. Run /model refresh to fetch "
                "the model catalog (or /model refresh <provider> for a single provider).",
                error=True,
            )
            return

        # --- Recent models section (top 5, always shown regardless of provider filter) ---
        recent_raw = get_recent_models()[:5]  # at most 5 most recent
        recent_model_set = set(recent_raw)
        recent_items: list[ListItem] = []
        recent_found: set[tuple[str, str]] = set()

        for m in all_models:
            key = (m.provider, m.id)
            if key in recent_model_set and key not in recent_found:
                recent_found.add(key)
                parts = [m.provider]
                if not m.supports_images:
                    parts.append("[no-vision]")
                caption = " ".join(parts)
                is_free = getattr(m, "is_free", False)
                free_suffix = " (free)" if is_free else ""
                base_label = f"{m.id}{free_suffix}"
                label = (
                    f"{base_label} ✓"
                    if m.id == self._runtime.model and m.provider == self._runtime.model_provider
                    else base_label
                )
                item = ListItem(value=m, label=label, description=caption)
                item.prefix = "↻ "
                item.prefix_style = "dim"
                recent_items.append(item)

        # Sort recent items by recency order (most recent first)
        recent_order = {key: i for i, key in enumerate(recent_raw)}
        recent_items.sort(key=lambda x: recent_order.get((x.value.provider, x.value.id), 999))

        # --- Rest of models, filtered by provider ---
        filter_slug = get_config().ui.model_provider_filter
        filtered_by_provider = False
        if filter_slug:
            filtered = [m for m in all_models if m.provider == filter_slug]
            # If the filter yields nothing but we had models before filtering,
            # the filter + empty cache is the culprit (e.g. fresh pip install
            # with filter=kilo/opencode where known_models is empty).
            if not filtered:
                filtered_by_provider = True
                all_models = filtered
            else:
                all_models = filtered
        if not all_models and not recent_items:
            chat = self.query_one("#chat-log", ChatLog)
            if filtered_by_provider:
                from vtx.ai.provider_catalog import get as get_provider_info

                info = get_provider_info(filter_slug)
                fetchable = bool(info and info.fetch_models)
                if fetchable:
                    chat.add_info_message(
                        f"No cached models for provider '{filter_slug}'. "
                        f"Run /model refresh {filter_slug} to fetch, or /provider "
                        f"to clear the filter and see all providers.",
                        error=True,
                    )
                else:
                    chat.add_info_message(
                        f"No models for provider '{filter_slug}' (filter active). "
                        f"Clear the filter with /provider to see all providers.",
                        error=True,
                    )
            else:
                chat.add_info_message(
                    "No models configured — cache is empty. Run /model refresh to fetch "
                    "the model catalog (or /model refresh <provider> for a single provider).",
                    error=True,
                )
            return

        other_items: list[ListItem] = []
        for m in all_models:
            key = (m.provider, m.id)
            if key in recent_found:
                continue  # already shown in recent section
            parts = [m.provider]
            if not m.supports_images:
                parts.append("[no-vision]")
            caption = " ".join(parts)
            is_free = getattr(m, "is_free", False)
            free_suffix = " (free)" if is_free else ""
            base_label = f"{m.id}{free_suffix}"
            label = (
                f"{base_label} ✓"
                if m.id == self._runtime.model and m.provider == self._runtime.model_provider
                else base_label
            )
            other_items.append(ListItem(value=m, label=label, description=caption))

        other_items.sort(key=lambda x: (x.value.provider, x.value.id))

        items = recent_items + other_items

        self._show_selection_picker(items, SelectionMode.MODEL)

    def _select_model(self, model) -> None:
        chat = self.query_one("#chat-log", ChatLog)
        info_bar = self.query_one("#compact-footer", InfoBar)

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

        from vtx.ai.model_fetcher import refresh_provider_models as refresh_legacy
        from vtx.ai.provider_catalog import get as get_provider_info
        from vtx.ai.provider_catalog import list_providers as list_catalog_providers

        if provider is not None:
            is_dynamic = get_dynamic_provider(provider) is not None
            info = get_provider_info(provider)
            is_legacy = bool(info and info.fetch_models)
            if not is_dynamic and not is_legacy:
                valid_dynamic = set(DYNAMIC_PROVIDERS)
                valid_legacy = set()
                for p in list_catalog_providers():
                    info = get_provider_info(p.slug)
                    if info and info.fetch_models:
                        valid_legacy.add(p.slug)
                valid = ", ".join(sorted(valid_dynamic | valid_legacy))
                chat.add_info_message(
                    f"Unknown provider: {provider}. Providers: {valid}", error=True
                )
                return
            chat.add_info_message(f"Refreshing {provider}...")
        else:
            if not DYNAMIC_PROVIDERS:
                chat.add_info_message("No providers to refresh", error=True)
                return
            chat.add_info_message(f"Refreshing all {len(DYNAMIC_PROVIDERS)} providers...")

        def _run() -> dict[str, int | str]:
            if provider is not None:
                best = 0
                last_err: str | None = None
                if is_dynamic:
                    try:
                        best = max(best, int(refresh_provider(provider)))
                    except Exception as exc:
                        last_err = str(exc)
                if is_legacy:
                    try:
                        best = max(best, int(refresh_legacy(provider)))
                    except Exception as exc:
                        last_err = str(exc)
                if best == 0 and last_err:
                    return {provider: -1, "_error": last_err}
                return {provider: best}
            # all providers: refresh both systems and merge
            try:
                dyn = dict(refresh_all_providers())
            except Exception as exc:
                dyn = {"_error": str(exc)}
            try:
                from vtx.ai.model_fetcher import refresh_all_provider_models
                from vtx.ai.provider_catalog import list_providers

                dynamic_slugs = set(DYNAMIC_PROVIDERS.keys())
                # Only legacy-refresh providers not already covered by the
                # dynamic fetcher; the legacy path is O(n) sequential and
                # would otherwise duplicate the concurrent dynamic sweep.
                legacy_only = [
                    p.slug
                    for p in list_providers()
                    if p.fetch_models and p.slug not in dynamic_slugs
                ]
                leg = dict(refresh_all_provider_models(legacy_only)) if legacy_only else {}
            except Exception:
                leg = {}
            merged: dict[str, int | str] = {}
            for k, v in dyn.items():
                if k == "_error":
                    continue
                dyn_val = int(v) if isinstance(v, int) else 0
                leg_val = int(leg.get(k, 0)) if isinstance(leg.get(k), int) else 0
                merged[k] = max(dyn_val, leg_val)
            for k, v in leg.items():
                if k not in merged:
                    merged[k] = v
            if "_error" in dyn:
                merged["_error"] = dyn["_error"]
            return merged

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
