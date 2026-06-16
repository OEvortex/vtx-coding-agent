"""Tests for the /provider slash command and the /model model_provider_filter."""

from contextlib import contextmanager

import pytest

from vtx.config import get_config
from vtx.llm import list_providers
from vtx.ui.commands import CommandsMixin
from vtx.ui.commands.providers import _ALL_SLUG
from vtx.ui.floating_list import ListItem
from vtx.ui.selection_mode import SelectionMode


class FakeChat:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self.statuses: list[str] = []

    def add_info_message(self, message: str, error: bool = False, warning: bool = False) -> None:
        if error:
            self.errors.append(message)
        elif warning:
            self.warnings.append(message)
        else:
            self.infos.append(message)

    def show_status(self, message: str) -> None:
        self.statuses.append(message)


class FakeFloatingList:
    def __init__(self) -> None:
        self.items: list[ListItem] = []
        self.selected: object = None
        self.searchable: bool | None = None

    def show(
        self, items: list[ListItem], searchable: bool = False, max_label_width: int | None = None
    ) -> None:
        self.items = items
        self.searchable = searchable

    def select_value(self, value: object) -> None:
        for item in self.items:
            if item.value == value:
                self.selected = value
                return


class FakeInputBox:
    def clear(self) -> None:
        pass

    def set_autocomplete_enabled(self, enabled: bool) -> None:
        pass

    def set_placeholder(self, value: str) -> None:
        pass

    def set_completing(self, completing: bool) -> None:
        pass

    def focus(self) -> None:
        pass


class FakeRuntime:
    def __init__(self) -> None:
        self.model: str = ""
        self.model_provider: str = ""


class FakeCommands(CommandsMixin):
    def __init__(self) -> None:
        self.chat = FakeChat()
        self.completion_list = FakeFloatingList()
        self.input_box = FakeInputBox()
        self._selection_mode: SelectionMode | None = None
        self._runtime = FakeRuntime()  # type: ignore
        self.notified: list[dict] = []

    @contextmanager
    def batch_update(self):
        yield

    def query_one(self, selector, widget_type):
        if selector == "#chat-log":
            return self.chat
        if selector == "#completion-list":
            return self.completion_list
        if selector == "#input-box":
            return self.input_box
        raise AssertionError(f"Unexpected selector: {selector}")

    def notify(self, message: str, **kwargs) -> None:
        self.notified.append({"message": message, **kwargs})

    def run_worker(self, coro, exclusive: bool = True):
        coro.close()

    def _is_chat_at_bottom(self) -> bool:
        return True

    def _restore_chat_scroll_after_refresh(self, was_at_bottom: bool) -> None:
        pass

    def _show_completion_list(
        self,
        items: list[ListItem],
        *,
        searchable: bool = False,
        max_label_width: int | None = None,
    ) -> None:
        self.completion_list.show(items, searchable=searchable, max_label_width=max_label_width)


def _known_provider_slug() -> str:
    providers = list_providers()
    if not providers:
        pytest.skip("No providers configured in provider.yaml")
    return providers[0].slug


def test_provider_opens_picker_with_all_row_first():
    fake = FakeCommands()
    fake._handle_provider_command("")

    assert fake._selection_mode == SelectionMode.PROVIDER
    assert fake.completion_list.items[0].value == _ALL_SLUG
    assert "All providers" in fake.completion_list.items[0].label
    catalog_slugs = {p.slug for p in list_providers()}
    picker_slugs = {item.value for item in fake.completion_list.items if item.value != _ALL_SLUG}
    assert catalog_slugs.issubset(picker_slugs)


def test_provider_picker_marks_current_filter():
    fake = FakeCommands()
    slug = _known_provider_slug()
    fake._select_provider_set(slug)
    assert get_config().ui.model_provider_filter == slug

    fake._handle_provider_command("")
    active_row = next(item for item in fake.completion_list.items if item.value == slug)
    all_row = fake.completion_list.items[0]
    assert "✓" in active_row.label
    assert "✓" not in all_row.label


def test_provider_picking_provider_sets_filter():
    fake = FakeCommands()
    slug = _known_provider_slug()

    fake._handle_provider_command("")
    fake._select_provider_set(slug)

    assert get_config().ui.model_provider_filter == slug
    assert any(slug in msg for msg in fake.chat.statuses)


def test_provider_picking_all_row_clears_filter():
    fake = FakeCommands()
    slug = _known_provider_slug()
    fake._select_provider_set(slug)
    assert get_config().ui.model_provider_filter == slug

    fake._handle_provider_command("")
    fake._select_provider_set(_ALL_SLUG)

    assert get_config().ui.model_provider_filter == ""
    assert any("cleared" in msg for msg in fake.chat.statuses)


def test_provider_reset_shortcut_clears_filter():
    fake = FakeCommands()
    slug = _known_provider_slug()
    fake._select_provider_set(slug)
    fake._handle_provider_command("reset")
    assert get_config().ui.model_provider_filter == ""


def test_provider_picker_ignores_unknown_arg_and_still_opens():
    fake = FakeCommands()
    fake._handle_provider_command("not-a-real-provider-xyz")
    assert fake._selection_mode == SelectionMode.PROVIDER


def test_provider_setter_rejects_unknown_slug():
    """Unknown provider via the setter clears the filter rather than persisting a typo."""
    from vtx import set_model_provider_filter

    set_model_provider_filter("not-a-real-provider-xyz")
    assert get_config().ui.model_provider_filter == ""


def test_model_picker_filters_to_one_provider(monkeypatch):
    from vtx.llm import Model

    fake = FakeCommands()
    fake._runtime.model = "model-a"
    fake._runtime.model_provider = "openai"

    captured_items: list[ListItem] = []

    def _capture(items, selection_mode, **_):
        captured_items.clear()
        captured_items.extend(items)

    monkeypatch.setattr(fake, "_show_selection_picker", _capture)

    def _stub_all_models():
        from vtx.llm.models import ApiType

        return [
            Model(
                id="model-a",
                provider="openai",
                api=ApiType(ApiType.OPENAI_SDK),
                base_url="",
                max_tokens=4096,
                supports_images=False,
                supports_thinking=False,
            ),
            Model(
                id="model-b",
                provider="kilo",
                api=ApiType(ApiType.OPENAI_SDK),
                base_url="",
                max_tokens=4096,
                supports_images=False,
                supports_thinking=False,
            ),
            Model(
                id="model-c",
                provider="anthropic",
                api=ApiType(ApiType.ANTHROPIC),
                base_url="",
                max_tokens=4096,
                supports_images=False,
                supports_thinking=False,
            ),
        ]

    monkeypatch.setattr("vtx.ui.commands.models.get_all_models", _stub_all_models)

    fake._select_provider_set("kilo")
    fake._handle_model_command("")

    provider_ids = {item.value.provider for item in captured_items}
    assert provider_ids == {"kilo"}

    fake._select_provider_set(_ALL_SLUG)
    fake._handle_model_command("")
    provider_ids = {item.value.provider for item in captured_items}
    assert provider_ids == {"openai", "kilo", "anthropic"}


@pytest.mark.asyncio
async def test_model_refresh_without_slug_refreshes_all_providers(monkeypatch):
    """`/model refresh` with no slug must refresh every provider, not just one."""
    import vtx.ui.commands.models as models_module

    captured: dict[str, object] = {}

    def fake_refresh_all():
        captured["called"] = "all"
        return {"kilo": 3, "openai": 5, "anthropic": 2}

    def fake_refresh_provider(name):
        captured["called"] = ("provider", name)
        return 1

    monkeypatch.setattr(models_module, "refresh_all_providers", fake_refresh_all)
    monkeypatch.setattr(models_module, "refresh_provider", fake_refresh_provider)

    fake = FakeCommands()
    await fake._refresh_dynamic_models(None)

    assert captured["called"] == "all"
    completed = [m for m in fake.chat.infos if m.startswith("Refresh complete")]
    assert completed
    assert "kilo: 3" in completed[0]
    assert "openai: 5" in completed[0]
    assert "anthropic: 2" in completed[0]


@pytest.mark.asyncio
async def test_model_refresh_with_slug_refreshes_only_that_provider(monkeypatch):
    import vtx.ui.commands.models as models_module

    captured: dict[str, object] = {}

    def fake_refresh_all():
        captured["called"] = "all"
        return {}

    def fake_refresh_provider(name):
        captured["called"] = ("provider", name)
        return 7

    monkeypatch.setattr(models_module, "refresh_all_providers", fake_refresh_all)
    monkeypatch.setattr(models_module, "refresh_provider", fake_refresh_provider)

    fake = FakeCommands()
    await fake._refresh_dynamic_models("kilo")

    assert captured["called"] == ("provider", "kilo")
    completed = [m for m in fake.chat.infos if m.startswith("Refresh complete")]
    assert completed
    assert "kilo: 7" in completed[0]


@pytest.mark.asyncio
async def test_model_refresh_unknown_slug_errors(monkeypatch):
    import vtx.ui.commands.models as models_module

    monkeypatch.setattr(models_module, "refresh_all_providers", lambda: {})
    monkeypatch.setattr(models_module, "refresh_provider", lambda n: 0)

    fake = FakeCommands()
    await fake._refresh_dynamic_models("not-a-real-provider")

    assert any("Unknown provider" in m for m in fake.chat.errors)


@pytest.mark.asyncio
async def test_model_refresh_with_slug_dispatches_via_run_worker(monkeypatch):
    """`/model refresh` should schedule the worker; `/model refresh <slug>` should too."""
    fake = FakeCommands()
    scheduled: list[bool] = []

    def _capture(coro, exclusive: bool = False):
        scheduled.append(exclusive)
        coro.close()

    monkeypatch.setattr(fake, "run_worker", _capture)

    fake._handle_model_command("refresh")
    fake._handle_model_command("refresh kilo")

    assert scheduled == [False, False]
