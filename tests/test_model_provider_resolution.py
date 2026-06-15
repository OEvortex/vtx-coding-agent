from vtx.llm.models import get_model


def test_get_model_prefers_provider_when_specified():
    gpt4o = get_model("gpt-4o", "openai")
    anthropic = get_model("claude-3-5-sonnet-20241022", "anthropic")

    assert gpt4o is not None
    assert anthropic is not None
    assert gpt4o.provider == "openai"
    assert anthropic.provider == "anthropic"
    assert gpt4o.api != anthropic.api


def test_get_model_falls_back_to_id_lookup():
    model = get_model("gpt-4o")

    assert model is not None
    assert model.provider == "openai"


def test_get_model_resolves_deepseek_models():
    model = get_model("deepseek-chat", "deepseek")

    assert model is not None
    assert model.provider == "deepseek"


def test_get_model_resolves_zhipu_models():
    model = get_model("glm-5.1", "zhipu")

    assert model is not None
    assert model.provider == "zhipu"
