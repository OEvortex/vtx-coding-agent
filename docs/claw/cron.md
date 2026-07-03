# Cron System

vtx-claw includes a built-in cron system for scheduling recurring tasks and one-shot reminders.

## Schedule Types

| Type | Description | Config |
|------|-------------|--------|
| `every` | Repeat at fixed interval | `every_seconds: 3600` |
| `cron` | Standard cron expression | `cron_expr: "0 9 * * *"` |
| `at` | One-shot at specific time | `at: "2024-12-25T09:00:00Z"` |

## Payload Types

| Type | Description |
|------|-------------|
| `agent_turn` | Sends a message to the agent |
| `system_event` | Internal system job (protected from removal) |

## Using the Cron Tool

### Add a Job

```
cron(action="add", message="Check GitHub PR status", every_seconds=3600)
```

```
cron(action="add", message="Daily standup summary", cron_expr="0 9 * * *", timezone="America/New_York")
```

```
cron(action="add", message="Remind me about meeting", at="2024-12-25T14:00:00Z")
```

### List Jobs

```
cron(action="list")
```

### Remove a Job

```
cron(action="remove", job_id="job-abc123")
```

## Cron Service

The `CronService` (`cron/service.py`) manages job lifecycle:

1. **Storage**: Jobs stored in `~/.vtx/claw/workspace/cron/jobs.json`
2. **Timer**: Single async task sleeps until next job is due (max 5 min intervals)
3. **Execution**: Jobs execute within their session context
4. **Persistence**: Jobs survive gateway restarts

### Job States

| Field | Description |
|-------|-------------|
| `next_run_at_ms` | Next scheduled execution |
| `last_run_at_ms` | Last execution time |
| `last_status` | `ok`, `error`, or `skipped` |
| `run_history` | Last 20 execution records |

### One-Shot Jobs

Jobs with `at` schedule auto-disable after execution. With `delete_after_run: true`, they're removed entirely.

### Session Delivery

Cron jobs deliver messages to the session specified by `session_key`. Jobs without a valid session key are auto-disabled.

## Multi-Instance Safety

When not running as a service, mutations append to `action.jsonl` instead of modifying the store directly. The timer merges the action log on each tick, preventing corruption from concurrent gateway instances.

## Cron Expression Syntax

Standard cron format: `minute hour day-of-month month day-of-week`

```
# Every day at 9 AM
0 9 * * *

# Every Monday at 10 AM
0 10 * * 1

# Every 15 minutes
*/15 * * * *

# First day of every month
0 0 1 * *
```

Use `croniter` for parsing with timezone support via `ZoneInfo`.
