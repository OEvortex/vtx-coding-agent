"""One SQLite database for everything Ctx writes itself.

Session transcripts, the token ledger, model limits, prompt history and the
session index used to be separate JSON/JSONL documents. Each had the same
three problems: a read cost that grew with the whole file (the ``/session``
picker parsed every session head, ``/usage`` re-read every log), a write cost
that grew with the whole file (the ledger rewrote 232 KB per sync), and no
index at all, so "the 20 most recent sessions" meant stat'ing everything.

This module owns the single ``ctx.db`` file those become. Rules it follows:

- **WAL, one writer.** Many readers, one writer at a time, no fsync per commit
  (``synchronous=NORMAL``): a crash costs the last transaction, not the file.
  Every writer holds ``BEGIN IMMEDIATE`` so contention is a short wait, not a
  half-written transaction.
- **Files stay for human content.** ``config.yml``, ``SKILL.md`` and
  ``AGENTS.md`` are written by people; they stay files. Anything the machine
  generates lives here.
- **Paths stay as identity.** A session keeps its historical
  ``sessions/<slug>/<ts>_<id>.jsonl`` path as a primary key even though no such
  file exists, so every existing caller that passes a path keeps working.
- **Legacy import is automatic and one-way.** On first open, any
  ``token_usage.json``, ``models_dev_limits.json`` or ``prompt-history.jsonl``
  beside the database is folded in and renamed to ``*.migrated``; the JSONL
  session tree is imported per project folder on first use.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Generator, Iterable
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DB_FILENAME = "ctx.db"
SCHEMA_VERSION = 1

#: A session file whose usage is folded in is never re-parsed unless it changed.
BUCKET_WIDTH = 5

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS session (
  path TEXT PRIMARY KEY,
  id TEXT NOT NULL,
  cwd TEXT NOT NULL,
  slug TEXT NOT NULL,
  created TEXT,
  modified TEXT,
  name TEXT,
  parent_id TEXT,
  first_message TEXT,
  message_count INTEGER NOT NULL DEFAULT 0,
  system_prompt TEXT,
  tools TEXT,
  initial_thinking_level TEXT
);
CREATE INDEX IF NOT EXISTS session_by_slug ON session(slug, modified DESC);
CREATE UNIQUE INDEX IF NOT EXISTS session_by_id ON session(id);
CREATE INDEX IF NOT EXISTS session_by_modified ON session(modified DESC);

CREATE TABLE IF NOT EXISTS entry (
  session_path TEXT NOT NULL,
  seq INTEGER NOT NULL,
  id TEXT NOT NULL,
  type TEXT NOT NULL,
  parent_id TEXT,
  ts TEXT,
  payload TEXT NOT NULL,
  PRIMARY KEY (session_path, seq)
) WITHOUT ROWID;
CREATE UNIQUE INDEX IF NOT EXISTS entry_by_id ON entry(session_path, id);

CREATE TABLE IF NOT EXISTS usage_file (
  key TEXT PRIMARY KEY,
  mtime REAL NOT NULL,
  size INTEGER NOT NULL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS usage_day (
  day TEXT NOT NULL,
  file_key TEXT NOT NULL,
  input INTEGER NOT NULL DEFAULT 0,
  output INTEGER NOT NULL DEFAULT 0,
  cache_read INTEGER NOT NULL DEFAULT 0,
  cache_write INTEGER NOT NULL DEFAULT 0,
  turns INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, file_key)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS usage_by_day ON usage_day(day);

-- Token usage for sessions that still exist is recorded per assistant
-- message as it is written, so a live session never needs a ledger row and
-- ``/usage`` never has to parse a transcript. ``usage_day`` is left for
-- sessions that were deleted: their tokens must outlive them.
CREATE TABLE IF NOT EXISTS usage_msg (
  session_path TEXT NOT NULL,
  entry_id TEXT NOT NULL,
  day TEXT NOT NULL,
  input INTEGER NOT NULL DEFAULT 0,
  output INTEGER NOT NULL DEFAULT 0,
  cache_read INTEGER NOT NULL DEFAULT 0,
  cache_write INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (session_path, entry_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS usage_msg_by_day ON usage_msg(day);

CREATE TABLE IF NOT EXISTS model_limit (
  provider TEXT NOT NULL,
  model_id TEXT NOT NULL,
  context INTEGER NOT NULL DEFAULT 0,
  output INTEGER NOT NULL DEFAULT 0,
  reasoning INTEGER NOT NULL DEFAULT 0,
  vision INTEGER NOT NULL DEFAULT 0,
  tools INTEGER NOT NULL DEFAULT 0,
  audio INTEGER NOT NULL DEFAULT 0,
  thinking TEXT,
  PRIMARY KEY (provider, model_id)
) WITHOUT ROWID;

-- Provider model catalogs, keyed by namespace so the two fetchers that used
-- to share one directory can no longer overwrite each other's files.
CREATE TABLE IF NOT EXISTS catalog (
  name TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  fetched_at REAL NOT NULL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS task (
  task_id TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  updated REAL NOT NULL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS prompt (
  ts REAL NOT NULL,
  text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS prompt_by_ts ON prompt(ts);
"""

_LOCAL = threading.local()
_SCHEMA_LOCK = threading.RLock()
_schema_ready: set[str] = set()


def db_path() -> Path:
    from ctx.core.paths import get_config_dir

    return get_config_dir() / DB_FILENAME


def connect() -> sqlite3.Connection:
    """The calling thread's connection, opened in WAL mode with the schema applied."""
    path = db_path()
    key = str(path)
    conn: sqlite3.Connection | None = getattr(_LOCAL, "conn", None)
    if conn is not None and getattr(_LOCAL, "key", None) == key:
        return conn

    path.parent.mkdir(parents=True, exist_ok=True)
    with suppress(OSError):
        path.parent.chmod(0o700)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    for pragma in (
        "journal_mode=WAL",
        # Durable against a process crash, cheap per commit; a power cut can
        # lose the last commit, which for a transcript is one message.
        "synchronous=NORMAL",
        "busy_timeout=10000",
        "temp_store=MEMORY",
        "foreign_keys=OFF",
        "cache_size=-32000",
        "mmap_size=268435456",
        "wal_autocheckpoint=1000",
    ):
        with suppress(sqlite3.Error):
            conn.execute(f"PRAGMA {pragma}")

    if key not in _schema_ready:
        with _SCHEMA_LOCK:
            if key not in _schema_ready:
                _ensure_schema(conn)
                _schema_ready.add(key)
    _LOCAL.conn = conn
    _LOCAL.key = key
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    row = conn.execute("SELECT v FROM meta WHERE k = 'schema_version'").fetchone()
    if row is None:
        conn.execute("INSERT INTO meta(k, v) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
    _backfill_usage_msg(conn)
    import_legacy_files(conn)


@contextmanager
def write() -> Generator[sqlite3.Connection]:
    """One writer's transaction; rolls back on any exception."""
    conn = connect()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def read() -> sqlite3.Connection:
    return connect()


# -- meta -----------------------------------------------------------------


def get_meta(key: str, conn: sqlite3.Connection | None = None) -> str | None:
    target = conn or read()
    row = target.execute("SELECT v FROM meta WHERE k = ?", (key,)).fetchone()
    return row["v"] if row else None


def set_meta(key: str, value: str, conn: sqlite3.Connection | None = None) -> None:
    target = conn or connect()
    target.execute(
        "INSERT INTO meta(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
        (key, value),
    )


# -- sessions -------------------------------------------------------------


def put_session(meta: dict[str, Any], conn: sqlite3.Connection | None = None) -> None:
    """Insert or replace a session's metadata row."""
    target = conn or connect()
    target.execute(
        """
        INSERT INTO session(path, id, cwd, slug, created, modified, name, parent_id,
                            first_message, message_count, system_prompt, tools,
                            initial_thinking_level)
        VALUES(:path, :id, :cwd, :slug, :created, :modified, :name, :parent_id,
               :first_message, :message_count, :system_prompt, :tools,
               :initial_thinking_level)
        ON CONFLICT(path) DO UPDATE SET
            id = excluded.id, cwd = excluded.cwd, slug = excluded.slug,
            created = excluded.created, modified = excluded.modified,
            name = COALESCE(excluded.name, session.name),
            parent_id = COALESCE(excluded.parent_id, session.parent_id),
            first_message = COALESCE(excluded.first_message, session.first_message),
            message_count = MAX(excluded.message_count, session.message_count),
            system_prompt = COALESCE(excluded.system_prompt, session.system_prompt),
            tools = COALESCE(excluded.tools, session.tools),
            initial_thinking_level = COALESCE(
                excluded.initial_thinking_level, session.initial_thinking_level)
        """,
        {
            "path": meta.get("path", ""),
            "id": meta.get("id", ""),
            "cwd": meta.get("cwd", ""),
            "slug": meta.get("slug", ""),
            "created": meta.get("created"),
            "modified": meta.get("modified"),
            "name": meta.get("name"),
            "parent_id": meta.get("parent_id"),
            "first_message": meta.get("first_message"),
            "message_count": int(meta.get("message_count") or 0),
            "system_prompt": meta.get("system_prompt"),
            "tools": meta.get("tools"),
            "initial_thinking_level": meta.get("initial_thinking_level"),
        },
    )


def touch_session(
    path: str,
    *,
    modified: str,
    message_count: int | None = None,
    first_message: str | None = None,
    name: str | None = None,
    parent_id: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Update only the fields that change per append.

    The picker reads ``modified``/``message_count``/``first_message``/``name``,
    so refreshing those four on every write keeps it exact without recomputing
    a session from its entries.
    """
    target = conn or connect()
    target.execute(
        """
        UPDATE session SET
          modified = ?,
          message_count = COALESCE(?, message_count),
          first_message = COALESCE(?, first_message),
          name = COALESCE(?, name),
          parent_id = COALESCE(?, parent_id)
        WHERE path = ?
        """,
        (modified, message_count, first_message, name, parent_id, path),
    )


def put_entry(
    session_path: str, seq: int, entry: dict[str, Any], conn: sqlite3.Connection | None = None
) -> None:
    """Insert or replace one entry at a sequence position."""
    target = conn or connect()
    payload = json.dumps(entry, separators=(",", ":"), ensure_ascii=False)
    target.execute(
        """
        INSERT INTO entry(session_path, seq, id, type, parent_id, ts, payload)
        VALUES(?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_path, seq) DO UPDATE SET
            id = excluded.id, type = excluded.type, parent_id = excluded.parent_id,
            ts = excluded.ts, payload = excluded.payload
        """,
        (
            session_path,
            seq,
            str(entry.get("id") or ""),
            str(entry.get("type") or ""),
            entry.get("parent_id"),
            entry.get("timestamp"),
            payload,
        ),
    )
    _track_entry_usage(target, session_path, entry)


def _track_entry_usage(conn: sqlite3.Connection, session_path: str, entry: dict[str, Any]) -> None:
    """Keep ``usage_msg`` level with the assistant messages being written."""
    entry_id = str(entry.get("id") or "")
    if not entry_id:
        return
    message = entry.get("message")
    usage = message.get("usage") if isinstance(message, dict) else None
    if not isinstance(usage, dict):
        conn.execute(
            "DELETE FROM usage_msg WHERE session_path = ? AND entry_id = ?",
            (session_path, entry_id),
        )
        return
    ts = entry.get("timestamp")
    day = None
    if isinstance(ts, str):
        with suppress(ValueError):
            day = datetime.fromisoformat(ts).astimezone().date().isoformat()
    if day is None:
        return
    conn.execute(
        "INSERT OR REPLACE INTO usage_msg(session_path, entry_id, day, input, output,"
        " cache_read, cache_write) VALUES(?, ?, ?, ?, ?, ?, ?)",
        (
            session_path,
            entry_id,
            day,
            int(usage.get("input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
            int(usage.get("cache_read_tokens") or 0),
            int(usage.get("cache_write_tokens") or 0),
        ),
    )


def prune_session_usage(session_path: str, conn: sqlite3.Connection) -> None:
    """Drop usage rows for entries a rewrite removed."""
    conn.execute(
        "DELETE FROM usage_msg WHERE session_path = ?1 AND entry_id NOT IN"
        " (SELECT id FROM entry WHERE session_path = ?1)",
        (str(session_path),),
    )


def session_meta(path: str) -> dict[str, Any] | None:
    row = read().execute("SELECT * FROM session WHERE path = ?", (str(path),)).fetchone()
    return dict(row) if row else None


def session_entries(path: str) -> list[dict[str, Any]]:
    rows = read().execute(
        "SELECT payload FROM entry WHERE session_path = ? ORDER BY seq", (str(path),)
    )
    return [json.loads(row["payload"]) for row in rows]


def session_entry_count(path: str) -> int:
    row = (
        read()
        .execute("SELECT COUNT(*) AS n FROM entry WHERE session_path = ?", (str(path),))
        .fetchone()
    )
    return int(row["n"]) if row else 0


def list_sessions(slug: str) -> list[dict[str, Any]]:
    """Every recorded session for one project folder, newest first."""
    rows = read().execute("SELECT * FROM session WHERE slug = ? ORDER BY modified DESC", (slug,))
    return [dict(row) for row in rows]


def list_all_sessions() -> list[dict[str, Any]]:
    """Every recorded session in every project folder, newest first."""
    rows = read().execute("SELECT * FROM session ORDER BY modified DESC")
    return [dict(row) for row in rows]


def recent_session(slug: str) -> dict[str, Any] | None:
    row = (
        read()
        .execute("SELECT * FROM session WHERE slug = ? ORDER BY modified DESC LIMIT 1", (slug,))
        .fetchone()
    )
    return dict(row) if row else None


def find_sessions_by_id(slug: str, session_id: str) -> list[dict[str, Any]]:
    """Exact id first, then id prefix, matching how ``-r`` resolves a session."""
    conn = read()
    exact = conn.execute(
        "SELECT * FROM session WHERE slug = ? AND lower(id) = ?", (slug, session_id.lower())
    ).fetchall()
    if exact:
        return [dict(row) for row in exact]
    prefix = conn.execute(
        "SELECT * FROM session WHERE slug = ? AND lower(id) LIKE ?",
        (slug, f"{session_id.lower()}%"),
    ).fetchall()
    return [dict(row) for row in prefix]


def fold_session_usage(path: str, conn: sqlite3.Connection) -> None:
    """Move a live session's tokens into the durable ledger before it is dropped."""
    conn.execute(
        """
        INSERT INTO usage_day(day, file_key, input, output, cache_read, cache_write, turns)
        SELECT day, ?, SUM(input), SUM(output), SUM(cache_read), SUM(cache_write), COUNT(*)
        FROM usage_msg WHERE session_path = ?
        GROUP BY day
        ON CONFLICT(day, file_key) DO UPDATE SET
            input = input + excluded.input,
            output = output + excluded.output,
            cache_read = cache_read + excluded.cache_read,
            cache_write = cache_write + excluded.cache_write,
            turns = turns + excluded.turns
        """,
        (str(path), str(path)),
    )


def delete_session(path: str) -> None:
    """Drop a session, keeping the tokens it spent."""
    with write() as conn:
        fold_session_usage(path, conn)
        conn.execute("DELETE FROM entry WHERE session_path = ?", (str(path),))
        conn.execute("DELETE FROM usage_msg WHERE session_path = ?", (str(path),))
        conn.execute("DELETE FROM session WHERE path = ?", (str(path),))


def all_session_paths() -> list[str]:
    return [row["path"] for row in read().execute("SELECT path FROM session")]


# -- usage ledger ---------------------------------------------------------

USAGE_FILES_SQL = "SELECT key, mtime, size FROM usage_file"


def usage_signatures() -> dict[str, tuple[float, int]]:
    return {row["key"]: (row["mtime"], row["size"]) for row in read().execute(USAGE_FILES_SQL)}


def put_usage_file(
    key: str,
    mtime: float,
    size: int,
    days: dict[str, list[int]],
    conn: sqlite3.Connection | None = None,
) -> None:
    """Record one session file's per-day buckets, replacing any previous ones."""
    target = conn or connect()
    target.execute(
        "INSERT INTO usage_file(key, mtime, size) VALUES(?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET mtime = excluded.mtime, size = excluded.size",
        (key, mtime, size),
    )
    target.execute("DELETE FROM usage_day WHERE file_key = ?", (key,))
    if days:
        target.executemany(
            "INSERT OR REPLACE INTO usage_day(day, file_key, input, output, cache_read,"
            " cache_write, turns) VALUES(?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    day,
                    key,
                    bucket[0] if len(bucket) > 0 else 0,
                    bucket[1] if len(bucket) > 1 else 0,
                    bucket[2] if len(bucket) > 2 else 0,
                    bucket[3] if len(bucket) > 3 else 0,
                    bucket[4] if len(bucket) > 4 else 0,
                )
                for day, bucket in days.items()
            ],
        )


def usage_days() -> dict[str, list[int]]:
    """``{iso_date: [input, output, cache_read, cache_write, turns]}``.

    Two sources, deliberately disjoint: live sessions are counted from their
    entries (so a session can never be booked twice, and never goes stale),
    deleted ones from ``usage_day`` where their tokens were folded on the way
    out.
    """
    merged: dict[str, list[int]] = {}
    rows = read().execute(
        "SELECT day, SUM(input) AS i, SUM(output) AS o, SUM(cache_read) AS cr,"
        " SUM(cache_write) AS cw, SUM(turns) AS t FROM ("
        "  SELECT day, input, output, cache_read, cache_write, turns FROM usage_day"
        "  UNION ALL"
        "  SELECT day, SUM(input), SUM(output), SUM(cache_read), SUM(cache_write),"
        "   COUNT(*) FROM usage_msg GROUP BY day"
        ") GROUP BY day"
    )
    for row in rows:
        merged[row["day"]] = [
            int(row["i"] or 0),
            int(row["o"] or 0),
            int(row["cr"] or 0),
            int(row["cw"] or 0),
            int(row["t"] or 0),
        ]
    return merged


def usage_file_total() -> int:
    row = read().execute("SELECT COUNT(*) AS n FROM usage_file").fetchone()
    return int(row["n"]) if row else 0


# -- model limits ---------------------------------------------------------


def replace_model_limits(rows: Iterable[tuple[Any, ...]], stamp: float) -> None:
    """Replace the cached models.dev catalog in one transaction."""
    with write() as conn:
        conn.execute("DELETE FROM model_limit")
        conn.executemany(
            "INSERT OR REPLACE INTO model_limit(provider, model_id, context, output,"
            " reasoning, vision, tools, audio, thinking)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            list(rows),
        )
        set_meta("models_dev_fetched_at", str(stamp), conn)


def model_limits() -> tuple[list[tuple[Any, ...]], float]:
    """Cached limits and the timestamp they were fetched at."""
    rows = read().execute(
        "SELECT provider, model_id, context, output, reasoning, vision, tools, audio,"
        " thinking FROM model_limit"
    )
    fetched = get_meta("models_dev_fetched_at")
    try:
        stamp = float(fetched or 0.0)
    except ValueError:
        stamp = 0.0
    return [tuple(row) for row in rows], stamp


# -- background tasks -----------------------------------------------------


def put_task(task_id: str, payload: dict[str, Any]) -> None:
    with write() as conn:
        conn.execute(
            "INSERT INTO task(task_id, payload, updated) VALUES(?, ?, ?)"
            " ON CONFLICT(task_id) DO UPDATE SET payload = excluded.payload,"
            " updated = excluded.updated",
            (task_id, json.dumps(payload, separators=(",", ":")), time.time()),
        )


def task_payloads(limit: int = 0) -> list[dict[str, Any]]:
    """Stored task records, newest first."""
    sql = "SELECT payload FROM task ORDER BY updated DESC"
    if limit > 0:
        sql += f" LIMIT {int(limit)}"
    out: list[dict[str, Any]] = []
    for row in read().execute(sql):
        try:
            data = json.loads(row["payload"])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


# -- provider catalogs ----------------------------------------------------


def get_catalog(name: str) -> dict[str, Any] | None:
    row = read().execute("SELECT payload FROM catalog WHERE name = ?", (name,)).fetchone()
    if row is None:
        return None
    try:
        data = json.loads(row["payload"])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def put_catalog(name: str, payload: dict[str, Any], fetched_at: float | None = None) -> None:
    with write() as conn:
        conn.execute(
            "INSERT INTO catalog(name, payload, fetched_at) VALUES(?, ?, ?)"
            " ON CONFLICT(name) DO UPDATE SET payload = excluded.payload,"
            " fetched_at = excluded.fetched_at",
            (name, json.dumps(payload, separators=(",", ":")), fetched_at or time.time()),
        )


# -- prompt history -------------------------------------------------------


def append_prompt(text: str, ts: float, limit: int = 0) -> None:
    with write() as conn:
        conn.execute("INSERT INTO prompt(ts, text) VALUES(?, ?)", (ts, text))
        if limit > 0:
            conn.execute(
                "DELETE FROM prompt WHERE rowid NOT IN "
                "(SELECT rowid FROM prompt ORDER BY ts DESC LIMIT ?)",
                (limit,),
            )


def recent_prompts(limit: int) -> list[str]:
    rows = read().execute("SELECT text FROM prompt ORDER BY ts DESC LIMIT ?", (int(limit),))
    return [row["text"] for row in rows]


def replace_prompts(entries: list[str], limit: int = 0) -> None:
    """Rewrite the whole history, keeping the newest ``limit`` prompts."""
    with write() as conn:
        conn.execute("DELETE FROM prompt")
        kept = entries[-limit:] if limit > 0 else entries
        conn.executemany(
            "INSERT INTO prompt(ts, text) VALUES(?, ?)",
            [(float(index), text) for index, text in enumerate(kept)],
        )


def clear_prompts() -> None:
    with write() as conn:
        conn.execute("DELETE FROM prompt")


# -- legacy import --------------------------------------------------------


def import_legacy_files(conn: sqlite3.Connection) -> None:
    """Fold the old one-document stores in, once, then park them as ``.migrated``.

    Called from schema creation, so a machine that has never run this build
    starts on the database and a machine that has runs it exactly once.
    """
    from ctx.core.paths import get_config_dir

    root = get_config_dir()

    ledger = root / "token_usage.json"
    if ledger.exists() and get_meta("legacy_usage_imported", conn) != "1":
        try:
            data = json.loads(ledger.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        files = data.get("files") if isinstance(data, dict) else None
        if isinstance(files, dict):
            for key, value in files.items():
                if not isinstance(value, dict):
                    continue
                days = value.get("days")
                if not isinstance(days, dict):
                    days = {}
                put_usage_file(
                    str(key),
                    float(value.get("mtime") or 0.0),
                    int(value.get("size") or 0),
                    {
                        str(day): list(bucket)
                        for day, bucket in days.items()
                        if isinstance(bucket, list)
                    },
                    conn,
                )
        set_meta("legacy_usage_imported", "1", conn)
        _park(ledger)

    limits = root / "models_dev_limits.json"
    if limits.exists() and get_meta("legacy_models_imported", conn) != "1":
        try:
            data = json.loads(limits.read_text(encoding="utf-8"))
            rows = _legacy_model_rows(data)
        except Exception:
            rows = []
        if rows:
            conn.execute("DELETE FROM model_limit")
            conn.executemany(
                "INSERT OR REPLACE INTO model_limit(provider, model_id, context, output,"
                " reasoning, vision, tools, audio, thinking)"
                " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            set_meta("models_dev_fetched_at", str(limits.stat().st_mtime), conn)
        set_meta("legacy_models_imported", "1", conn)
        _park(limits)

    prompts = root / "prompt-history.jsonl"
    if prompts.exists() and get_meta("legacy_prompts_imported", conn) != "1":
        try:
            lines = prompts.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        entries: list[tuple[float, str]] = []
        for index, line in enumerate(lines):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, str):
                text, ts = data, float(index)
            elif isinstance(data, dict) and isinstance(data.get("text"), str):
                text, ts = data["text"], float(data.get("ts") or index)
            else:
                text, ts = "", 0.0
            if text.strip():
                entries.append((ts, text))
        conn.executemany("INSERT INTO prompt(ts, text) VALUES(?, ?)", entries)
        set_meta("legacy_prompts_imported", "1", conn)
        _park(prompts)


def _backfill_usage_msg(conn: sqlite3.Connection) -> None:
    """Seed ``usage_msg`` from entries written before it existed."""
    if get_meta("usage_msg_built", conn) == "1":
        return
    conn.execute(
        """
        INSERT OR REPLACE INTO usage_msg(session_path, entry_id, day, input, output,
                                         cache_read, cache_write)
        SELECT session_path, id,
               COALESCE(date(json_extract(payload, '$.timestamp'), 'localtime'),
                        date(ts, 'localtime')),
               COALESCE(json_extract(payload, '$.message.usage.input_tokens'), 0),
               COALESCE(json_extract(payload, '$.message.usage.output_tokens'), 0),
               COALESCE(json_extract(payload, '$.message.usage.cache_read_tokens'), 0),
               COALESCE(json_extract(payload, '$.message.usage.cache_write_tokens'), 0)
        FROM entry
        WHERE type = 'message'
          AND json_extract(payload, '$.message.usage') IS NOT NULL
        """
    )
    set_meta("usage_msg_built", "1", conn)


def _legacy_model_rows(data: Any) -> list[tuple[Any, ...]]:
    """Flatten a models.dev document into ``model_limit`` rows."""
    rows: list[tuple[Any, ...]] = []
    if not isinstance(data, dict):
        return rows
    for provider, provider_data in data.items():
        if not isinstance(provider_data, dict):
            continue
        models = provider_data.get("models")
        if not isinstance(models, dict):
            continue
        for model_id, info in models.items():
            if not isinstance(info, dict):
                continue
            limit = info.get("limit") if isinstance(info.get("limit"), dict) else {}
            modalities = info.get("modalities") if isinstance(info.get("modalities"), dict) else {}
            inputs = modalities.get("input") or []
            outputs = modalities.get("output") or []
            thinking = info.get("reasoning_options")
            rows.append(
                (
                    str(provider).strip().lower(),
                    str(model_id),
                    int(limit.get("context") or 0),
                    int(limit.get("output") or 0),
                    1 if info.get("reasoning") else 0,
                    1 if "image" in inputs else 0,
                    1 if info.get("tool_call") else 0,
                    1 if ("audio" in inputs or "audio" in outputs) else 0,
                    json.dumps(thinking) if thinking else None,
                )
            )
    return rows


def _park(path: Path) -> None:
    with suppress(OSError):
        path.rename(path.with_name(f"{path.name}.migrated"))


# -- maintenance ----------------------------------------------------------


def backup_to(target: Path) -> None:
    """Copy the live database into *target* through SQLite's own backup API."""
    import sqlite3 as _sqlite3

    target.parent.mkdir(parents=True, exist_ok=True)
    with suppress(OSError):
        target.unlink()
    dest = _sqlite3.connect(target)
    try:
        connect().backup(dest)
    finally:
        dest.close()


def vacuum() -> None:
    connect().execute("VACUUM")


def stats() -> dict[str, int]:
    conn = read()

    def _count(sql: str) -> int:
        row = conn.execute(sql).fetchone()
        return int(row[0]) if row else 0

    out = {
        "sessions": _count("SELECT COUNT(*) FROM session"),
        "entries": _count("SELECT COUNT(*) FROM entry"),
        "usage_files": _count("SELECT COUNT(*) FROM usage_file"),
        "usage_days": _count("SELECT COUNT(*) FROM usage_day"),
        "model_limits": _count("SELECT COUNT(*) FROM model_limit"),
        "prompts": _count("SELECT COUNT(*) FROM prompt"),
    }
    with suppress(OSError):
        out["bytes"] = db_path().stat().st_size
    return out


def now() -> str:
    return datetime.now(UTC).isoformat()
