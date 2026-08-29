from contextlib import contextmanager

import pytest

from vtx.ai.oauth.codex import OpenAICredentials
from vtx.tui.commands import CommandsMixin
from vtx.tui.commands import auth as commands
from vtx.tui.floating_list import ListItem
from vtx.tui.selection_mode import SelectionMode


class FakeChat:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.infos: list[str] = []
        self.warnings: list[str] = []

    def add_info_message(self, message: str, error: bool = False, warning: bool = False) -> None:
        if error:
            self.errors.append(message)
        elif warning:
            self.warnings.append(message)
        else:
            self.infos.append(message)


class FakeFloatingList:
    def __init__(self) -> None:
        self.items: list[ListItem] = []
        self.searchable: bool | None = None

    def show(
        self, items: list[ListItem], searchable: bool = False, max_label_width: int | None = None
    ) -> None:
        self.items = items
        self.searchable = searchable


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


class FakeCommands(CommandsMixin):
    def __init__(self) -> None:
        self.chat = FakeChat()
        self.completion_list = FakeFloatingList()
        self.input_box = FakeInputBox()
        self._selection_mode = None
        self.workers: list[tuple[object, bool]] = []

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

    def run_worker(self, coro, exclusive: bool = True):
        self.workers.append((coro, exclusive))
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


@pytest.mark.asyncio
async def test_openai_login_flow_skips_oauth_when_credentials_are_valid(monkeypatch):
    fake = FakeCommands()
    creds = OpenAICredentials(
        refresh="refresh", access="access", expires=9_999_999_999_999, account_id="account"
    )
    login_calls: list[int] = []

    async def get_credentials() -> OpenAICredentials:
        return creds

    async def login(**kwargs) -> None:
        login_calls.append(1)

    monkeypatch.setattr(commands, "get_valid_openai_credentials", get_credentials)
    monkeypatch.setattr(commands, "openai_login", login)

    await fake._openai_login_flow()

    assert login_calls == []
    assert fake.chat.infos == ["Already logged in to OpenAI"]


@pytest.mark.asyncio
async def test_openai_login_flow_starts_oauth_for_stale_saved_credentials(monkeypatch):
    fake = FakeCommands()
    login_calls: list[int] = []

    async def get_credentials() -> None:
        return None

    async def login(**kwargs) -> None:
        login_calls.append(1)

    monkeypatch.setattr(commands, "has_saved_openai_credentials", lambda: True)
    monkeypatch.setattr(commands, "get_valid_openai_credentials", get_credentials)
    monkeypatch.setattr(commands, "openai_login", login)

    await fake._openai_login_flow()

    assert login_calls == [1]
    assert fake.chat.warnings == ["Your saved OpenAI session is no longer valid."]
    assert fake.chat.infos == [
        "Successfully logged in to OpenAI!\nYou can now use /model to select openai-codex models."
    ]


@pytest.mark.asyncio
async def test_copilot_login_flow_starts_oauth_for_stale_saved_credentials(monkeypatch):
    fake = FakeCommands()
    login_calls: list[int] = []

    async def get_token() -> None:
        return None

    async def login(**kwargs) -> None:
        login_calls.append(1)

    monkeypatch.setattr(commands, "has_saved_copilot_credentials", lambda: True)
    monkeypatch.setattr(commands, "get_copilot_token", get_token)
    monkeypatch.setattr(commands, "copilot_login", login)

    await fake._copilot_login_flow()

    assert login_calls == [1]
    assert fake.chat.warnings == ["Your saved GitHub Copilot session is no longer valid."]
    assert fake.chat.infos == [
        "Successfully logged in to GitHub Copilot!\n"
        "You can now use /model to select Copilot models."
    ]


def test_select_login_provider_schedules_login_workers():
    fake = FakeCommands()

    fake._select_login_provider("openai")
    fake._select_login_provider("github-copilot")

    assert len(fake.workers) == 2
    assert all(exclusive is False for _, exclusive in fake.workers)
    assert fake.chat.infos == []


def test_login_picker_shows_auth_method_choices(monkeypatch):
    fake = FakeCommands()
    monkeypatch.setattr(commands, "has_saved_openai_credentials", lambda: True)
    monkeypatch.setattr(commands, "has_saved_copilot_credentials", lambda: False)
    monkeypatch.setattr(commands, "has_saved_cline_credentials", lambda: False)
    monkeypatch.setattr(commands, "list_providers", lambda: [])

    fake._handle_login_command("")

    assert fake._selection_mode == SelectionMode.LOGIN_METHOD
    rows = [(item.value, item.label) for item in fake.completion_list.items]
    assert rows == [("oauth", "OAuth"), ("apikey", "API Key"), ("keyless", "Local / Keyless")]
    assert "GitHub Copilot" in fake.completion_list.items[0].description


def test_login_method_oauth_shows_only_oauth_providers(monkeypatch):
    fake = FakeCommands()
    monkeypatch.setattr(commands, "has_saved_openai_credentials", lambda: True)
    monkeypatch.setattr(commands, "has_saved_copilot_credentials", lambda: False)
    monkeypatch.setattr(commands, "has_saved_cline_credentials", lambda: False)

    fake._select_login_method("oauth")

    assert fake._selection_mode == SelectionMode.LOGIN
    rows = [(item.value, item.label, item.description) for item in fake.completion_list.items]
    assert rows == [
        ("github-copilot", "GitHub Copilot", "oauth login"),
        ("openai", "OpenAI (ChatGPT/Codex)", "saved credentials"),
        ("cline", "Cline (WorkOS)", "oauth login"),
    ]


def test_login_method_apikey_excludes_keyless_providers(monkeypatch):
    """Keyless providers belong in their own sub-picker, not the API-key one."""
    from vtx.ai.oauth.dynamic import DynamicProviderStatus
    from vtx.ai.provider_catalog import ProviderInfo

    fake = FakeCommands()
    monkeypatch.setattr(
        commands,
        "list_providers",
        lambda: [
            ProviderInfo(
                slug="anthropic",
                display_name="Anthropic",
                description="Direct Anthropic API.",
                family="anthropic",
                base_url="https://api.anthropic.com",
                api_key_env="ANTHROPIC_API_KEY",
            ),
            ProviderInfo(
                slug="ollama",
                display_name="Ollama (local)",
                description="Local models served by Ollama.",
                family="openai_compat",
                base_url="http://localhost:11434/v1",
                api_key_env=None,
                api_key_optional=True,
                is_local=True,
            ),
        ],
    )
    monkeypatch.setattr(
        commands,
        "get_provider_status",
        lambda slug: DynamicProviderStatus(
            provider=slug,
            env_var=None,
            has_env_key=False,
            has_stored_key=False,
            api_key_optional=False,
        ),
    )

    fake._select_login_method("apikey")

    assert fake._selection_mode == SelectionMode.LOGIN_API_KEY
    rows = [(item.value, item.label) for item in fake.completion_list.items]
    assert rows == [("anthropic", "Anthropic")]


def test_login_method_keyless_shows_only_keyless_providers(monkeypatch):
    from vtx.ai.provider_catalog import ProviderInfo

    fake = FakeCommands()
    monkeypatch.setattr(
        commands,
        "list_providers",
        lambda: [
            ProviderInfo(
                slug="anthropic",
                display_name="Anthropic",
                description="Direct Anthropic API.",
                family="anthropic",
                base_url="https://api.anthropic.com",
                api_key_env="ANTHROPIC_API_KEY",
            ),
            ProviderInfo(
                slug="ollama",
                display_name="Ollama (local)",
                description="Local models served by Ollama.",
                family="openai_compat",
                base_url="http://localhost:11434/v1",
                api_key_env=None,
                api_key_optional=True,
                is_local=True,
            ),
        ],
    )

    fake._select_login_method("keyless")

    assert fake._selection_mode == SelectionMode.LOGIN_KEYLESS
    rows = [(item.value, item.label, item.description) for item in fake.completion_list.items]
    assert rows == [("ollama", "Ollama (local)", "local")]


def test_select_keyless_provider_refreshes_models(monkeypatch):
    class FakeCoro:
        def close(self) -> None:
            pass

    fake = FakeCommands()
    monkeypatch.setattr(
        commands.AuthCommands,
        "_refresh_after_api_key",
        lambda self, provider_id: fake.chat.infos.append(f"refresh {provider_id}") or FakeCoro(),
    )

    fake._select_keyless_provider("ollama")

    assert fake.chat.infos == ["Fetching models for ollama...", "refresh ollama"]


def test_select_apikey_provider_prompts_for_key(monkeypatch):
    from vtx.ai.oauth.dynamic import DynamicProviderStatus
    from vtx.ai.provider_catalog import ProviderInfo

    fake = FakeCommands()
    monkeypatch.setattr(
        commands,
        "get_provider_info",
        lambda slug: ProviderInfo(
            slug=slug,
            display_name="Anthropic",
            description="Direct Anthropic API.",
            family="anthropic",
            base_url="https://api.anthropic.com",
            api_key_env="ANTHROPIC_API_KEY",
        ),
    )
    monkeypatch.setattr(
        commands,
        "get_provider_status",
        lambda slug: DynamicProviderStatus(
            provider=slug,
            env_var="ANTHROPIC_API_KEY",
            has_env_key=False,
            has_stored_key=False,
            api_key_optional=False,
        ),
    )
    monkeypatch.setattr(commands, "get_dynamic_api_key", lambda slug: None)
    monkeypatch.setattr(
        commands.AuthCommands,
        "_prompt_for_api_key",
        lambda self, provider_id: fake.chat.infos.append(f"prompting {provider_id}"),
    )

    fake._select_apikey_provider("anthropic")

    assert fake.chat.infos == ["prompting anthropic"]
