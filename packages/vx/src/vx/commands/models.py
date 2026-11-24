"""``/model`` and ``/provider`` — the catalog surface, ported from ctx-code.

Differences from ctx-code are in the widget only: vx has one picker with a
right-hand metadata column, so the provider goes there and the recent models
keep their ``↻`` prefix in the label.
"""

from __future__ import annotations

import asyncio

from ctx.ai import (
    DYNAMIC_PROVIDERS,
    Model,
    get_all_models,
    get_dynamic_provider,
    list_providers,
    refresh_all_providers,
    refresh_provider,
)
from ctx.ai.context_length import safe_max_output_tokens
from ctx.ai.dynamic_models import find_dynamic_model
from ctx.ai.models import get_model
from ctx.ai.provider_catalog import get as get_provider_info
from ctx.ai.provider_catalog import get_catalog_models_for, provider_api_type
from ctx.core.config import get_config, get_recent_models, set_model_provider_filter

from vx.commands.base import CommandSupport
from vx.tui import footer

#: The "clear the filter" row in the provider picker.
ALL_PROVIDERS = "__all__"


def _parse_hidden_entries(entries: list[str]) -> tuple[set[str], set[tuple[str, str]]]:
    """Split hidden-model entries into provider names and (provider, model) pairs."""
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
    # -- /model ----------------------------------------------------------

    def _handle_model_command(self, args: str) -> None:
        stripped = args.strip()
        if stripped.startswith("refresh"):
            provider = stripped[len("refresh") :].strip() or None
            self._spawn(self._refresh_dynamic_models(provider))
            return
        if stripped:
            self._select_model_by_name(stripped)
            return
        self._open_model_menu()

    def _visible_models(self) -> list[Model] | None:
        try:
            hidden_providers, hidden_combos = _parse_hidden_entries(
                list(get_config().ui.hidden_models)
            )
            filter_slug = get_config().ui.model_provider_filter
            # A scoped picker reads one provider out of the catalog store: the
            # full list is ~6.4k models and takes seconds to materialise, and
            # everything outside the filter would be thrown away anyway. Names
            # only — the chosen model's limits are resolved on selection.
            scoped = get_catalog_models_for(filter_slug, enrich=False) if filter_slug else None
            models = scoped if scoped is not None else list(get_all_models(enrich=False))
        except Exception as exc:
            self._fail(f"failed to load model catalog: {exc}", topic="model")
            return None
        if hidden_providers or hidden_combos or any(m.hidden for m in models):
            current_id = getattr(self.runtime, "model", None)
            current_provider = getattr(self.runtime, "model_provider", None)
            models = [
                m
                for m in models
                if (not m.hidden and not _is_model_hidden(m, hidden_providers, hidden_combos))
                or (m.id == current_id and m.provider == current_provider)
            ]
        return models

    def _open_model_menu(self) -> None:
        models = self._visible_models()
        if models is None:
            return
        if not models:
            self._fail(
                "no models configured — the cache is empty. run /model refresh to fetch "
                "the catalog (or /model refresh <provider> for one provider).",
                topic="model",
            )
            return

        filter_slug = get_config().ui.model_provider_filter
        current_id = getattr(self.runtime, "model", "")
        current_provider = getattr(self.runtime, "model_provider", None)

        recent_pairs = get_recent_models()[:5]
        by_pair = {(m.provider, m.id): m for m in models}

        rows: list[footer.MenuItem] = []
        seen: set[tuple[str, str]] = set()
        # Recent models are resolved one at a time: a scoped picker does not
        # hold other providers' catalogs, and a recent model from another
        # provider is still one keystroke away.
        recent_models: list[Model] = []
        for pair in recent_pairs:
            model = by_pair.get(pair) or get_model(pair[1], pair[0])
            if model is None:
                model = find_dynamic_model(pair[1], pair[0])
            if model is not None:
                recent_models.append(model)
        for model in recent_models:
            seen.add((model.provider, model.id))
            rows.append(self._model_item(model, current_id, current_provider, recent=True))

        others = [m for m in models if (m.provider, m.id) not in seen]
        if filter_slug:
            others = [m for m in others if m.provider == filter_slug]
            if not others and not rows:
                self._no_models_for_filter(filter_slug)
                return
        others.sort(key=lambda m: (m.provider, m.id))

        rows.extend(self._model_item(model, current_id, current_provider) for model in others)
        if not rows:
            self._fail(
                "no models configured — the cache is empty. run /model refresh to fetch "
                "the catalog.",
                topic="model",
            )
            return

        index = next(
            (i for i, row in enumerate(rows) if row.value == f"{current_provider}::{current_id}"),
            0,
        )
        self._open_menu(
            rows,
            noun="Models",
            on_select=self._on_model_pick,
            variants=footer.CATALOG_HINT_VARIANTS,
            selected=index,
            filterable=True,
        )

    def _no_models_for_filter(self, filter_slug: str) -> None:
        info = get_provider_info(filter_slug)
        if info is not None and info.fetch_models:
            self._fail(
                f"no cached models for provider '{filter_slug}'. run "
                f"/model refresh {filter_slug} to fetch, or /provider to clear the filter.",
                topic="model",
            )
        else:
            self._fail(
                f"no models for provider '{filter_slug}' (filter active). "
                "clear the filter with /provider to see every provider.",
                topic="model",
            )

    def _model_item(
        self, model: Model, current_id: str, current_provider: str | None, *, recent: bool = False
    ) -> footer.MenuItem:
        is_current = model.id == current_id and model.provider == current_provider
        free_suffix = " (free)" if getattr(model, "is_free", False) else ""
        prefix = "↻ " if recent else ""
        label = f"{prefix}{model.id}{free_suffix}{' ✓' if is_current else ''}"
        facts = [model.provider]
        if not model.supports_images:
            facts.append("no-vision")
        if model.context_window:
            facts.append(f"{model.context_window // 1000}k")
        return footer.MenuItem(
            label=label,
            detail="" if model.supports_images else "text only",
            metadata=" · ".join(facts),
            value=f"{model.provider}::{model.id}",
        )

    def _on_model_pick(self, item: footer.MenuItem) -> None:
        provider, _, model_id = (item.value or "").partition("::")
        if not provider or not model_id:
            return
        model = get_model(model_id, provider) or find_dynamic_model(model_id, provider)
        if model is None:
            self._fail(f"model not found: {provider}::{model_id}", topic="model")
            return
        self._select_model(model)

    def _select_model_by_name(self, spec: str) -> None:
        """Select by ``model``, ``provider::model``, or build an ad-hoc model."""
        provider: str | None = None
        model_id = spec
        if "::" in spec:
            candidate, rest = spec.split("::", 1)
            if rest and (
                get_provider_info(candidate) is not None
                or get_dynamic_provider(candidate) is not None
            ):
                provider = candidate
                model_id = rest

        model = get_model(model_id, provider)
        if model is None and provider is not None:
            model = find_dynamic_model(model_id, provider)
        if model is None and provider is None:
            current_provider = getattr(self.runtime, "model_provider", None)
            model = get_model(spec, current_provider) or find_dynamic_model(spec, current_provider)
            if model is None:
                model = get_model(spec, None) or find_dynamic_model(spec, None)

        if model is None:
            if provider is None:
                self._fail(
                    f"unknown model: {spec}. use provider::model for an uncataloged model "
                    "(for example /model respan::gemini-4-carbon:free).",
                    topic="model",
                )
                return
            model = self._build_adhoc_model(provider, model_id)
            if model is None:
                self._fail(
                    f"unknown provider: {provider}. cannot select an uncataloged model.",
                    topic="model",
                )
                return
        self._select_model(model)

    @staticmethod
    def _build_adhoc_model(provider_slug: str, model_id: str) -> Model | None:
        info = get_provider_info(provider_slug)
        if info is not None:
            context_window = 128_000
            return Model(
                id=model_id,
                provider=provider_slug,
                api=provider_api_type(info),
                base_url=info.base_url or "",
                max_tokens=info.max_tokens or safe_max_output_tokens(context_window),
                supports_images=bool(info.supports_vision),
                supports_thinking=bool(info.supports_thinking),
                context_window=context_window,
                supports_tools=bool(info.supports_tools),
            )
        dyn = get_dynamic_provider(provider_slug)
        if dyn is None:
            return None
        context_window = 128_000
        return Model(
            id=model_id,
            provider=provider_slug,
            api=dyn.api,
            base_url=dyn.base_url,
            max_tokens=safe_max_output_tokens(context_window),
            supports_images=False,
            supports_thinking=False,
            context_window=context_window,
            supports_tools=True,
        )

    def _select_model(self, model: Model) -> None:
        rt = self.runtime
        if rt is None:
            return
        try:
            rt.switch_model(model)
        except ValueError as exc:
            self._fail(str(exc), topic="model")
            return
        self._context_total = model.context_window  # type: ignore[attr-defined]
        try:
            from ctx.core.config import set_last_selected

            set_last_selected(
                model.id,
                model.provider,
                getattr(rt, "thinking_level", None),
                getattr(rt, "agent_name", None),
            )
        except Exception:
            pass
        self._ok(f"model changed to {model.id} ({model.provider})", topic="model")
        self._refresh_stats(force=True)

    async def _refresh_dynamic_models(self, provider: str | None) -> None:
        from ctx.ai.model_fetcher import refresh_provider_models as refresh_legacy
        from ctx.ai.provider_catalog import list_providers as list_catalog_providers

        if provider is not None:
            is_dynamic = get_dynamic_provider(provider) is not None
            info = get_provider_info(provider)
            is_legacy = bool(info and info.fetch_models)
            if not is_dynamic and not is_legacy:
                legacy = [
                    p.slug
                    for p in list_catalog_providers()
                    if (info := get_provider_info(p.slug)) is not None and info.fetch_models
                ]
                valid = sorted(set(DYNAMIC_PROVIDERS) | set(legacy))
                self._fail(f"unknown provider: {provider}. providers: {', '.join(valid)}")
                return
            self._notice(f"refreshing {provider}…", topic="model")
        else:
            if not DYNAMIC_PROVIDERS:
                self._fail("no providers to refresh", topic="model")
                return
            self._notice(f"refreshing all {len(DYNAMIC_PROVIDERS)} providers…", topic="model")

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

            try:
                dyn = dict(refresh_all_providers())
            except Exception as exc:
                dyn = {"_error": str(exc)}
            try:
                from ctx.ai.model_fetcher import refresh_all_provider_models

                dynamic_slugs = set(DYNAMIC_PROVIDERS.keys())
                legacy_only = [
                    p.slug
                    for p in list_catalog_providers()
                    if p.fetch_models and p.slug not in dynamic_slugs
                ]
                legacy = dict(refresh_all_provider_models(legacy_only)) if legacy_only else {}
            except Exception:
                legacy = {}
            merged: dict[str, int | str] = {}
            for key, value in dyn.items():
                if key == "_error":
                    continue
                legacy_value = int(legacy.get(key, 0)) if isinstance(legacy.get(key), int) else 0
                merged[key] = max(int(value) if isinstance(value, int) else 0, legacy_value)
            for key, value in legacy.items():
                merged.setdefault(key, value)
            if "_error" in dyn:
                merged["_error"] = dyn["_error"]
            return merged

        try:
            result = await asyncio.to_thread(_run)
        except Exception as exc:
            self._fail(f"refresh failed: {exc}", topic="model")
            return

        error = result.pop("_error", None)
        if error:
            self._fail(f"refresh failed: {error}", topic="model")
            return
        if not result:
            self._ok("refresh complete (no providers returned models)", topic="model")
            return
        rows = [(str(name), f"{count} models") for name, count in sorted(result.items())]
        self._report("Model catalog refreshed", [(f"{len(rows)} providers", rows)])

    # -- /provider -------------------------------------------------------

    def _handle_provider_command(self, args: str) -> None:
        providers = list_providers()
        if not providers:
            self._fail("no providers configured", topic="provider")
            return
        if args.strip().lower() in ("reset", "all", "clear"):
            self._set_provider_filter("")
            return

        active = get_config().ui.model_provider_filter
        items = [
            footer.MenuItem(
                label="All providers ✓" if not active else "All providers",
                detail=(
                    "currently showing every provider in /model"
                    if not active
                    else "show every provider in /model"
                ),
                value=ALL_PROVIDERS,
            )
        ]
        for provider in providers:
            enabled = bool(active) and provider.slug == active
            items.append(
                footer.MenuItem(
                    label=f"{provider.slug} ✓" if enabled else provider.slug,
                    detail=provider.display_name,
                    metadata="filter active" if enabled else "",
                    value=provider.slug,
                )
            )
        index = next((i for i, item in enumerate(items) if item.value == active), 0)
        self._open_menu(
            items,
            noun="Providers",
            on_select=lambda item: self._set_provider_filter(
                "" if item.value == ALL_PROVIDERS else (item.value or item.label)
            ),
            selected=index,
            filterable=True,
        )

    def _set_provider_filter(self, slug: str) -> None:
        set_model_provider_filter(slug)
        if slug:
            self._ok(f"provider set to {slug} — /model will show only {slug} models")
        else:
            self._ok("provider filter cleared — /model now shows all providers")
