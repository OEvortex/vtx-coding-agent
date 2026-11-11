from types import SimpleNamespace

import pytest

from vtx.coding_agent.acp import AcpServer, _Session


class _Runtime:
    def __init__(self):
        self.model = "old-model"
        self.model_provider = "old-provider"
        self.thinking_level = "low"
        self.effective_thinking_levels = ["low", "high"]
        self.agent_registry = SimpleNamespace(
            agents=[SimpleNamespace(definition=SimpleNamespace(name="reviewer"))],
            active=SimpleNamespace(definition=SimpleNamespace(name="reviewer")),
            by_name=lambda name: (
                SimpleNamespace(definition=SimpleNamespace(name=name))
                if name == "reviewer"
                else None
            ),
        )
        self.switched_models = []
        self.thinking_levels = []
        self.active_agents = []

    def switch_model(self, model):
        self.switched_models.append(model)
        self.model = model.id
        self.model_provider = model.provider

    def set_thinking_level(self, level):
        self.thinking_levels.append(level)
        self.thinking_level = level

    def set_active_agent(self, name):
        self.active_agents.append(name)


@pytest.mark.asyncio
async def test_session_config_options_update_live_runtime(monkeypatch):
    model = SimpleNamespace(id="new-model", provider="test-provider", base_url="")
    monkeypatch.setattr("vtx.ai.models.get_model", lambda model_id, provider: model)
    server = AcpServer()
    session = _Session("/workspace")
    session.acp_id = "s1"
    session.runtime = _Runtime()
    server._sessions["s1"] = session

    await server.session_set_config_option(
        {"sessionId": "s1", "configId": "model", "value": "test-provider:new-model"}
    )
    await server.session_set_config_option(
        {"sessionId": "s1", "configId": "thinking", "value": "high"}
    )
    await server.session_set_config_option({"sessionId": "s1", "configId": "agent", "value": ""})

    assert session.runtime.model == "new-model"
    assert session.runtime.model_provider == "test-provider"
    assert session.runtime.switched_models == [model]
    assert session.runtime.thinking_levels == ["high"]
    assert session.runtime.active_agents == [None]


@pytest.mark.asyncio
async def test_session_config_options_reject_unsupported_values():
    server = AcpServer()
    session = _Session("/workspace")
    session.acp_id = "s1"
    session.runtime = _Runtime()
    server._sessions["s1"] = session

    assert "_error" in await server.session_set_config_option(
        {"sessionId": "s1", "configId": "mode", "value": "plan"}
    )
    assert "_error" in await server.session_set_config_option(
        {"sessionId": "s1", "configId": "agent", "value": "missing"}
    )
    assert "_error" in await server.session_set_config_option(
        {"sessionId": "s1", "configId": "missing", "value": "value"}
    )
    assert "_error" in await server.session_set_config_option(
        {"sessionId": "s1", "configId": "thinking", "value": "max"}
    )
    assert session.runtime.active_agents == []


@pytest.mark.asyncio
async def test_config_option_request_for_unknown_session_is_rejected():
    result = await AcpServer().session_set_config_option(
        {"sessionId": "missing", "configId": "mode", "value": "default"}
    )

    assert result == {"_error": "unknown session"}
