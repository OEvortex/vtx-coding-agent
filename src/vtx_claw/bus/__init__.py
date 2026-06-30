"""Message bus module for decoupled channel-agent communication."""

from vtx_claw.bus.events import InboundMessage, OutboundMessage
from vtx_claw.bus.queue import MessageBus

__all__ = ["InboundMessage", "MessageBus", "OutboundMessage"]
