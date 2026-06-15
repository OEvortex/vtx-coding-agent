from vtx.llm.base import is_local_base_url, resolve_api_key
from vtx.llm.models import ApiType
from vtx.llm.providers import PROVIDER_API_BY_NAME, get_provider_class, resolve_provider_api_type
from vtx.runtime import default_base_url_for_provider


def test_resolve_provider_api_type_openai():
    api = resolve_provider_api_type("openai")
    assert api == ApiType(ApiType.OPENAI_SDK)


def test_resolve_provider_api_type_anthropic():
    api = resolve_provider_api_type("anthropic")
    assert api == ApiType(ApiType.ANTHROPIC)


def test_resolve_provider_api_type_unknown_defaults_to_openai():
    api = resolve_provider_api_type("nonexistent")
    assert api == ApiType(ApiType.OPENAI_SDK)


def test_resolve_provider_api_type_none_defaults_to_openai():
    api = resolve_provider_api_type(None)
    assert api == ApiType(ApiType.OPENAI_SDK)


def test_get_provider_class_openai():
    cls = get_provider_class(ApiType(ApiType.OPENAI_SDK))
    assert cls.name == "openai"


def test_get_provider_class_anthropic():
    cls = get_provider_class(ApiType(ApiType.ANTHROPIC))
    assert cls.name == "anthropic"


def test_provider_api_by_name_has_expected_providers():
    assert "openai" in PROVIDER_API_BY_NAME
    assert "anthropic" in PROVIDER_API_BY_NAME
    assert "deepseek" in PROVIDER_API_BY_NAME


def test_is_local_base_url():
    assert is_local_base_url("http://localhost:11434/v1") is True
    assert is_local_base_url("http://127.0.0.1:8080") is True
    assert is_local_base_url("https://api.openai.com/v1") is False


def test_resolve_api_key_from_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    key = resolve_api_key(None, env_vars=("OPENAI_API_KEY",))
    assert key == "test-key"


def test_resolve_api_key_explicit():
    key = resolve_api_key("explicit-key", env_vars=("OPENAI_API_KEY",))
    assert key == "explicit-key"


def test_resolve_api_key_none_when_not_set(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    key = resolve_api_key(None, env_vars=("OPENAI_API_KEY",))
    assert key is None


def test_resolve_api_key_auto_local():
    key = resolve_api_key(None, base_url="http://localhost:8080", auth_mode="auto")
    assert key == "vtx-local"


def test_resolve_api_key_none_mode():
    key = resolve_api_key(None, auth_mode="none")
    assert key == "vtx-local"


def test_default_base_url_for_provider_dynamic():
    url = default_base_url_for_provider("kilo")
    assert url is not None
    assert "kilo" in url


def test_default_base_url_for_provider_unknown():
    url = default_base_url_for_provider("nonexistent")
    assert url is None
