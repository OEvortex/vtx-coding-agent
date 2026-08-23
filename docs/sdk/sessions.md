# SDK Sessions

Sessions give runs memory across invocations. Any object implementing the protocol works:

```python
class Session(Protocol):
    session_id: str
    async def get_items(self, limit: int | None = None) -> list[dict]: ...
    async def add_items(self, items: list[dict]) -> None: ...
    async def pop_item(self) -> dict | None: ...
    async def clear_session(self) -> None: ...
```

## Built-in backends

```python
from ai.agent.sdk import InMemorySession, JSONLSession

ses = InMemorySession()                        # process-local
ses = JSONLSession()                           # memory-backed until given a path
ses = JSONLSession("runs/agent-42.jsonl")      # persisted JSONL file

# Resume later — same path reloads the existing file:
ses = JSONLSession("runs/agent-42.jsonl")
```

`JSONLSession` stores one JSON item per line — the same format the CLI uses, so SDK and TUI sessions are interchangeable.

## Using them

```python
result = await Runner.run(agent, "Hi", session=ses)
result = await Runner.run(agent, "What did I just say?", session=ses)   # remembers
```

History is loaded, merged with the new input (`RunConfig.session_input_callback` can filter), and appended after the run. `SessionSettings(limit=N)` caps how many items load per run.

Runnable example: [`examples/sdk/05_sessions.py`](../../examples/sdk/05_sessions.py).
