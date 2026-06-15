"""Minimal extension example. Logs a line on agent_end.

Drop this file into ``~/.vtx/agent/extensions/hello.py`` (or any
``.vtx/extensions/hello.py``) and the next vtx run will print
``hello extension loaded`` on startup, plus ``agent ended: stop``
when the agent finishes a turn.
"""

from vtx.extensions import AGENT_END, SESSION_START


def register(api):
    api.notify("loaded", level="info")

    @api.on(SESSION_START)
    def _start(event, payload):
        api.notify("session starting")

    @api.on(AGENT_END)
    def _done(event, payload):
        stop = payload.get("stop_reason", "unknown")
        api.notify(f"agent ended: {stop}")
