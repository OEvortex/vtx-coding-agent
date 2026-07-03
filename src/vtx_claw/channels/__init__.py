"""Chat channels module with plugin architecture."""

from vtx_claw.channels.base import BaseChannel
from vtx_claw.channels.manager import ChannelManager

__all__ = ["BaseChannel", "ChannelManager"]
