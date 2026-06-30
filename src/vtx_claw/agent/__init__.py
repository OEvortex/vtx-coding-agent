"""Agent core module — uses vtx-backed components where applicable."""

from vtx_claw.agent.context import ContextBuilder
from vtx_claw.agent.hook import AgentHook, AgentHookContext, AgentRunHookContext, CompositeHook
from vtx_claw.agent.loop import AgentLoop
from vtx_claw.agent.memory import MemoryStore
from vtx_claw.agent.skills import SkillsLoader
from vtx_claw.agent.subagent import SubagentManager

# vtx-backed types available through the bridge:

__all__ = [
    "AgentHook",
    "AgentHookContext",
    "AgentLoop",
    "AgentRunHookContext",
    "CompositeHook",
    "ContextBuilder",
    "MemoryStore",
    "SkillsLoader",
    "SubagentManager",
]
