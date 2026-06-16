# Sessions

The SDK ships a `Session` Protocol for memory. Two backends are
provided out of the box; custom backends (Redis, SQLite, DynamoDB,
…) implement the protocol.

## Protocol

```python
class Session(Protocol):
    session_id: str

    async def get_items(self, limit: int | None = None) -> list[dict]:
        ...

    async def add_items(self, items: list[dict]) -> None:
        ...

    async def pop_item(self) -> dict | None:
        ...

    async def clear_session(self) -> None:
        ...
```

The shape matches OpenAI's `SessionABC`. `get_items` returns the
history as input-item dicts (the same format used by `Runner.run(..., input=...)`).

## Built-in backends

### InMemorySession

Non-persistent, fast, ideal for tests.

```python
from vtx.sdk import InMemorySession

session = InMemorySession()  # generates a session_id
result = await Runner.run(agent, "hello", session=session)
```

### JSONLSession

Append-only JSONL on disk, interoperable with Vtx's TUI/headless mode.
A session created by the SDK can be resumed from the TUI, and vice
versa.

```python
from vtx.sdk import JSONLSession

session = JSONLSession("~/sdk-sessions/demo.jsonl")
result = await Runner.run(agent, "hello", session=session)
```

The file format is the same `~/.vtx/sessions/<safe-cwd>/<id>.jsonl`
that the TUI writes.

## Per-run overrides

`RunConfig.session_input_callback` lets you customize how the
session's history is merged with the new turn's input:

```python
def keep_recent_only(history, new_input):
    return history[-10:] + new_input

result = await Runner.run(
    agent,
    input,
    session=session,
    run_config=RunConfig(session_input_callback=keep_recent_only),
)
```

The callback receives **copies** of both lists, so you can mutate
them safely. Only the items in `new_input` are persisted as fresh
turn input; the history items you reorder or filter are not
re-saved.

## Writing your own backend

```python
from vtx.sdk import Session

class RedisSession:
    def __init__(self, session_id: str, redis_client):
        self.session_id = session_id
        self._client = redis_client

    async def get_items(self, limit=None):
        raw = await self._client.get(f"sdk:session:{self.session_id}")
        items = json.loads(raw) if raw else []
        return items[-limit:] if limit else items

    async def add_items(self, items):
        # … push to Redis with TTL …

    async def pop_item(self):
        # … rpop …

    async def clear_session(self):
        await self._client.delete(f"sdk:session:{self.session_id}")
```

The class does not need to inherit from anything — Python's structural
typing accepts it as a `Session` because of the protocol.
