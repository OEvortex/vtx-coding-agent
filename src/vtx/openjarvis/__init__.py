"""OpenJarvis — modular gateway for Jarvis-style device control.

Subpackages:
    channels  — chat/channel adapters (telegram, whatsapp, discord, etc.)
    cron      — scheduled jobs (alias: corn)
    gateways  — gateway transports (websocket, http, etc. — alias: gatways)
    pairing   — device pairing & auth
    commands  — command registry & dispatch
    utils     — shared helpers
"""

from vtx.openjarvis.version import VERSION as __version__

__all__ = ["__version__"]
