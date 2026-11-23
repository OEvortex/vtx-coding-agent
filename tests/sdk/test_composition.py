"""Runner's plugin-composition surface: tools/prompt resolution via services."""

from __future__ import annotations

from types import SimpleNamespace

from ctx.agent.sdk.agent import Agent
from ctx.agent.sdk.run_config import RunConfig
from ctx.agent.sdk.runner import _composition_for, _composition_prompt, _merge_composition_tools


class _Comp:
    """Minimal stand-in for a plugin ``Context`` (``get`` service lookup)."""

    def __init__(self, services: dict):
        self._services = services

    def get(self, key, default=None):
        return self._services.get(key, default)


class _Host:
    def __init__(self, base):
        self.base = base


def _tool(name):
    return SimpleNamespace(name=name, prompt_guidelines=())


def test_composition_for_accepts_host_and_context():
    comp = _Comp({})
    assert _composition_for(RunConfig(plugins=_Host(comp))) is comp
    assert _composition_for(RunConfig(plugins=comp)) is comp
    assert _composition_for(RunConfig()) is None


def test_merge_composition_tools_unions_and_dedupes():
    agent_tool = _tool("read")
    pool_tool = _tool("bash")
    dup_tool = _tool("read")
    service = SimpleNamespace(assemble=lambda: [pool_tool, dup_tool])
    comp = _Comp({"tools": service})

    merged = _merge_composition_tools([agent_tool], comp)
    assert [t.name for t in merged] == ["read", "bash"]

    # No composition -> passthrough
    assert _merge_composition_tools([agent_tool], None) == [agent_tool]


def test_composition_prompt_uses_prompts_service():
    agent = Agent(name="t", instructions="Be brief.", output_type=None)
    captured = {}

    def build(cwd, tools, **kwargs):
        captured.update(kwargs)
        return "COMPOSED"

    comp = _Comp({"prompts": SimpleNamespace(build=build)})
    prompt = _composition_prompt(comp, agent, None, [], "/tmp")
    assert prompt == "COMPOSED"
    assert captured["base_content"] == "Be brief."

    # No prompts service -> None (caller falls back to agent prompt)
    assert _composition_prompt(_Comp({}), agent, None, [], "/tmp") is None
    assert _composition_prompt(None, agent, None, [], "/tmp") is None
