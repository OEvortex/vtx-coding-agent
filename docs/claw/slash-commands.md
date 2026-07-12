# Slash Commands

agenite-claw includes 14 built-in slash commands for in-chat control. These are registered via the command router (`command/builtin.py`).

## Command Reference

### `/new`

Archive current session and start fresh.

```
/new
```

- Archives the current session JSONL
- Creates a new session with the same key
- Reloads project context

### `/stop`

Cancel active tasks and drain pending queue.

```
/stop
```

- Cancels all running tool calls
- Kills active subagents
- Drains pending user messages

### `/restart`

Restart the agenite-claw process.

```
/restart
```

- Executes `os.execv` to replace the current process
- Preserves all state (sessions, config)

### `/status`

Show runtime information.

```
/status
```

Displays:
- Version
- Active model and provider
- Token usage (current session)
- Uptime
- Search usage
- Active tasks

### `/model`

Show current model preset or switch models.

```
/model              # Show current preset
/model fast         # Switch to "fast" preset
/model openai/gpt-4o  # Switch to specific model
```

### `/history`

Display recent conversation messages.

```
/history            # Show last 10 messages
/history 20         # Show last 20 messages (max 50)
```

### `/goal`

Declare a sustained objective.

```
/goal Refactor the authentication module to use OAuth2
```

- Injects a prompt for the agent to use `long_task`/`complete_goal`
- Goal persists across turns until completed
- Use `/goal status` to check progress
- Use `/goal pause` to pause
- Use `/goal resume` to resume
- Use `/goal clear` to cancel

### `/dream`

Trigger memory consolidation manually.

```
/dream
```

- Runs the Dream memory consolidation process
- Summarizes recent conversations
- Updates `MEMORY.md` in the workspace

### `/dream-log`

Show the latest Dream commit diff.

```
/dream-log          # Show latest diff
/dream-log <sha>    # Show specific commit
```

### `/dream-restore`

Restore Dream memory to an earlier version.

```
/dream-restore      # List available commits
/dream-restore <sha>  # Restore to specific commit
```

### `/skill`

List all enabled skills with descriptions.

```
/skill
```

Displays skill name, description, and whether it's always-loaded.

### `/help`

Show the command palette.

```
/help
```

Lists all available commands with brief descriptions.

### `/pairing`

Manage DM pairing (approve/deny/list/revoke).

```
/pairing list                    # Show pending requests
/pairing approve ABCD-1234       # Approve a pairing code
/pairing deny ABCD-1234          # Deny a pairing code
/pairing revoke 123456           # Revoke access by user ID
/pairing revoke telegram 123456  # Revoke by channel + user ID
```

See [pairing.md](pairing.md) for details.

## Command Routing

Commands are routed through a three-tier dispatch system:

1. **Priority**: Exact match with highest priority
2. **Exact**: Exact command name match
3. **Prefix**: Commands that start with the input

The router supports `@username` suffixes for bots in groups (e.g., `/status@agenite_claw`).

## Custom Commands

Slash commands can be added via the extension system or skills. See [skills.md](skills.md) for `register_cmd: true` in SKILL.md frontmatter.
