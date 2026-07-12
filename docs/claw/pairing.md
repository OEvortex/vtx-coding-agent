# Device Pairing

agenite-claw includes a device pairing system for controlling who can interact with the bot via DMs.

## How Pairing Works

1. **New user DMs the bot** → Bot generates a pairing code
2. **Owner approves the code** → User is added to the approved list
3. **Future messages** → Processed normally

## Pairing Flow

```
User: "Hello!"
Bot: "To start chatting, please get approval from the owner.
      Your pairing code is: ABCD-1234"

Owner: "/pairing approve ABCD-1234"
Bot: "User approved! Hello, how can I help you?"
```

## Pairing Codes

- **Format**: `XXXX-XXXX` (8 characters)
- **Generation**: Cryptographically random (`secrets.choice`)
- **TTL**: 10 minutes (configurable)
- **Storage**: `~/.vtx/claw/pairing.json`

## Storage Format

```json
{
  "approved": {
    "telegram": ["123456", "789012"],
    "discord": ["user_id_1"]
  },
  "pending": {
    "ABCD-1234": {
      "channel": "telegram",
      "sender_id": "345678",
      "created_at": "2024-01-01T00:00:00Z"
    }
  }
}
```

## Commands

### List Pending Requests

```
/pairing list
```

Shows all pending pairing requests with expiry times.

### Approve a Code

```
/pairing approve ABCD-1234
```

Moves the sender from pending to approved.

### Deny a Code

```
/pairing deny ABCD-1234
```

Rejects the pairing code.

### Revoke Access

```
/pairing revoke 123456
```

or

```
/pairing revoke telegram 123456
```

Removes an approved sender.

## Channel Policies

Each channel can configure its DM access policy:

| Policy | Description |
|--------|-------------|
| `open` | All DMs are processed (no pairing required) |
| `pairing` | DMs require pairing approval |

Example:

```json
{
  "channels": {
    "telegram": {
      "allow_from": ["*"],
      "group_policy": "mention"
    },
    "slack": {
      "dm": {
        "policy": "pairing",
        "allow_from": ["U01234567"]
      }
    }
  }
}
```

## Thread Safety

All pairing operations use a module-level `threading.Lock` for thread safety. This is acceptable at private-assistant scale with sub-millisecond JSON operations.

## Garbage Collection

Expired pending codes are automatically cleaned up on every store load.
