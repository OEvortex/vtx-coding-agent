"""Message bus module for decoupled channel-agent communication."""

from agenite_claw.bus.events import InboundMessage, OutboundMessage
from agenite_claw.bus.queue import MessageBus

__all__ = ["InboundMessage", "MessageBus", "OutboundMessage"]
