"""Chat channels module with plugin architecture."""

from agenite_claw.channels.base import BaseChannel
from agenite_claw.channels.manager import ChannelManager

__all__ = ["BaseChannel", "ChannelManager"]
