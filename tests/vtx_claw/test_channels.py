from __future__ import annotations

import pytest

from vtx_claw.channels.slack import SlackAdapter
from vtx_claw.channels.signal_irc import IRCAdapter


def test_slack_adapter_creation():
    a = SlackAdapter()
    assert a.id == "slack"
    assert a.label == "Slack"


@pytest.mark.asyncio
async def test_slack_send_text():
    a = SlackAdapter()
    await a.start({"bot_token": "t", "enabled": True})
    r = await a.send_text("C123", "hello")
    assert r == "C123"


def test_irc_adapter_creation():
    a = IRCAdapter()
    assert a.id == "irc"
    assert not a.is_running()
