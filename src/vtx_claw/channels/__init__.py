from __future__ import annotations

from vtx_claw.channels.base import ChannelPlugin, InboundMessage, OutboundMessage
from vtx_claw.channels.discord import DiscordAdapter
from vtx_claw.channels.feishu import FeishuAdapter
from vtx_claw.channels.signal_irc import IRCAdapter
from vtx_claw.channels.slack import SlackAdapter
from vtx_claw.channels.telegram import TelegramAdapter
from vtx_claw.channels.whatsapp import WhatsAppAdapter

CHANNEL_REGISTRY: dict[str, type[ChannelPlugin]] = {
    "telegram": TelegramAdapter,
    "feishu": FeishuAdapter,
    "discord": DiscordAdapter,
    "whatsapp": WhatsAppAdapter,
    "slack": SlackAdapter,
    "irc": IRCAdapter,
}

__all__ = [
    "CHANNEL_REGISTRY",
    "ChannelPlugin",
    "DiscordAdapter",
    "FeishuAdapter",
    "IRCAdapter",
    "InboundMessage",
    "OutboundMessage",
    "SlackAdapter",
    "TelegramAdapter",
    "WhatsAppAdapter",
]
