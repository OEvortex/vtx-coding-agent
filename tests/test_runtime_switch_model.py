import pytest

from vtx.core.types import Message, ToolDefinition
from vtx.llm.base import BaseProvider, LLMStream, ProviderConfig
from vtx.llm.models import ApiType
from vtx.runtime import ConversationRuntime, create_provider


class _FakeProvider(BaseProvider):
    name = "fake"

    async def _stream_impl(
        self,
        messages: list[Message],
        *,
        system_prompt: str | None = None,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMStream:
        return LLMStream()

    def should_retry_for_error(self, error: Exception) -> bool:
        return False


def _runtime_with_provider(provider: BaseProvider) -> ConversationRuntime:
    runtime = ConversationRuntime(
        cwd="/test/project",
        model=provider.config.model,
        model_provider=provider.config.provider,
        api_key="test-key",
        base_url=None,
        thinking_level="high",
        tools=[],
    )
    runtime.provider = provider
    return runtime


@pytest.mark.parametrize(
    ("api_type", "provider_name", "expected_name"),
    [
        (ApiType(ApiType.OPENAI_SDK), "openai", "openai"),
        (ApiType(ApiType.ANTHROPIC), "anthropic", "anthropic"),
    ],
)
def test_create_provider_does_not_require_oauth_credentials(
    api_type: ApiType, provider_name: str, expected_name: str, tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    provider = create_provider(api_type, ProviderConfig(model="gpt-4o", provider=provider_name))

    assert provider.name == expected_name


def test_initialize_creates_agent_without_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    runtime = ConversationRuntime(
        cwd=str(tmp_path),
        model="gpt-4o",
        model_provider="openai",
        api_key=None,
        base_url=None,
        thinking_level="high",
        tools=[],
    )

    result = runtime.initialize()

    assert result.provider_error is None
    assert runtime.provider is not None
    assert runtime.session is not None
    assert runtime.agent is not None


def test_switch_model_recreates_provider_when_base_url_changes(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    provider1 = create_provider(
        ApiType(ApiType.OPENAI_SDK),
        ProviderConfig(model="gpt-4o", provider="openai", base_url="https://api.openai.com/v1"),
    )
    runtime = _runtime_with_provider(provider1)

    from vtx.llm.models import Model

    new_model = Model(
        id="deepseek-chat",
        provider="deepseek",
        api=ApiType(ApiType.OPENAI_SDK),
        base_url="https://api.deepseek.com/v1",
        max_tokens=8192,
        supports_images=False,
        supports_thinking=False,
    )
    runtime.switch_model(new_model)

    assert runtime.model == "deepseek-chat"
    assert runtime.model_provider == "deepseek"
    assert runtime.provider is not None
    assert runtime.provider.config.base_url == "https://api.deepseek.com/v1"


def test_switch_model_reuses_provider_when_base_url_unchanged(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    provider1 = create_provider(
        ApiType(ApiType.OPENAI_SDK),
        ProviderConfig(model="gpt-4o", provider="openai", base_url="https://api.openai.com/v1"),
    )
    runtime = _runtime_with_provider(provider1)
    original_provider = runtime.provider

    from vtx.llm.models import Model

    new_model = Model(
        id="gpt-4o-mini",
        provider="openai",
        api=ApiType(ApiType.OPENAI_SDK),
        base_url="https://api.openai.com/v1",
        max_tokens=16384,
        supports_images=True,
        supports_thinking=False,
    )
    runtime.switch_model(new_model)

    assert runtime.model == "gpt-4o-mini"
    assert runtime.provider is original_provider


def test_switch_model_to_different_api_type_creates_new_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    provider1 = create_provider(
        ApiType(ApiType.OPENAI_SDK),
        ProviderConfig(model="gpt-4o", provider="openai", base_url="https://api.openai.com/v1"),
    )
    runtime = _runtime_with_provider(provider1)

    from vtx.llm.models import Model

    new_model = Model(
        id="claude-sonnet-4-20250514",
        provider="anthropic",
        api=ApiType(ApiType.ANTHROPIC),
        base_url="https://api.anthropic.com",
        max_tokens=8192,
        supports_images=True,
        supports_thinking=True,
    )
    runtime.switch_model(new_model)

    assert runtime.model == "claude-sonnet-4-20250514"
    assert runtime.provider is not None
    assert runtime.provider.name == "anthropic"
