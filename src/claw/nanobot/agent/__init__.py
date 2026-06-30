"""Agent core module — uses vtx-backed components where applicable."""

from nanobot.agent.context import ContextBuilder
from nanobot.agent.hook import AgentHook, AgentHookContext, AgentRunHookContext, CompositeHook
from nanobot.agent.loop import AgentLoop
from nanobot.agent.memory import MemoryStore
from nanobot.agent.skills import SkillsLoader
from nanobot.agent.subagent import SubagentManager

# vtx-backed types available through the bridge:

__all__ = [
    "AgentHook",
    "AgentHookContext",
    "AgentRunHookContext",
    "AgentLoop",
    "CompositeHook",
    "ContextBuilder",
    "MemoryStore",
    "SkillsLoader",
    "SubagentManager",
]
