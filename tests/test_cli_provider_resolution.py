from vtx.llm import resolve_provider_api_type
from vtx.llm.models import ApiType
from vtx.runtime import default_base_url_for_api


def test_resolve_provider_api_type_known_provider():
    assert resolve_provider_api_type("openai") == ApiType(ApiType.OPENAI_SDK)
    assert resolve_provider_api_type("anthropic") == ApiType(ApiType.ANTHROPIC)


def test_resolve_provider_api_type_unknown_provider_defaults():
    result = resolve_provider_api_type("invalid-provider")
    assert result == ApiType(ApiType.OPENAI_SDK)


def testdefault_base_url_for_api_openai(monkeypatch):
    monkeypatch.setenv("VTX_BASE_URL", "http://localhost:1234/v1")
    assert default_base_url_for_api(ApiType(ApiType.OPENAI_SDK)) == "http://localhost:1234/v1"


def testdefault_base_url_for_api_anthropic():
    assert default_base_url_for_api(ApiType(ApiType.ANTHROPIC)) is None
