"""OpenJarvis — modular gateway for Jarvis-style device control.

Subpackages:
    channels  — chat/channel adapters (telegram, whatsapp, discord, etc.)
    cron      — scheduled jobs (alias: corn)
    gateways  — gateway transports (websocket, http, etc. — alias: gatways)
    pairing   — device pairing & auth
    commands  — command registry & dispatch
    utils     — shared helpers
"""

from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("vtx-coding-agent")
except Exception:
    __version__ = "0.2.0"

__all__ = ["__version__"]
