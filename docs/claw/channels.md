# Channels

agenite-claw connects to 16 chat platforms via a unified channel abstraction. Each channel is a thin adapter that translates platform-specific messages into a common `InboundMessage` format and sends responses back via `OutboundMessage`.

## How Channels Work

1. **Discovery**: Channels are discovered via `pkgutil` + `entry_points(group="agenite_claw.channels")`.
2. **Configuration**: Each channel reads its config from `channels.<name>` in the JSON config.
3. **Lifecycle**: `ChannelManager` calls `start()` on each enabled channel at gateway startup.
4. **Permission**: `is_allowed(sender_id)` checks the `allow_from` list or the pairing store.
5. **Session Key**: Each channel derives a unique session key (e.g., `telegram:123456`).

## BaseChannel Contract

All channels inherit from `BaseChannel` (`channels/base.py`):

```python
class BaseChannel:
    name: str               # e.g. "telegram"
    display_name: str       # e.g. "Telegram"

    async def start()       # Initialize and begin receiving messages
    async def stop()        # Graceful shutdown
    async def send(msg)     # Send outbound message

    # Optional overrides
    async def send_delta(msg)          # Streaming partial updates
    async def send_reasoning_delta(msg) # Streaming reasoning
    async def send_file_edit_events(events)
    def is_allowed(sender_id) -> bool  # Permission check
```

---

## Telegram

**File**: `channels/telegram.py`

Telegram bot using long polling or webhook mode.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable Telegram channel |
| `token` | string | `""` | Bot token from @BotFather |
| `mode` | `"polling"` \| `"webhook"` | `"polling"` | Connection mode |
| `allow_from` | list | `[]` | Allowed user IDs (`["*"]` for all) |
| `proxy` | string \| null | `null` | Proxy URL |
| `reply_to_message` | bool | `false` | Reply to user's original message |
| `react_emoji` | string | `"👀"` | Emoji reaction on received message |
| `group_policy` | `"open"` \| `"mention"` | `"mention"` | Group response policy |
| `streaming` | bool | `true` | Stream response updates |
| `inline_keyboards` | bool | `false` | Enable inline keyboard buttons |
| `rich_messages` | bool | `false` | Use Bot API 10.1 sendRichMessage |
| `stream_edit_interval` | float | `0.6` | Min seconds between message edits |
| `webhook_url` | string | `""` | Public HTTPS URL (webhook mode) |
| `webhook_listen_host` | string | `"127.0.0.1"` | Webhook server bind address |
| `webhook_listen_port` | int | `8081` | Webhook server port |
| `webhook_path` | string | `"/telegram"` | Webhook URL path |
| `webhook_secret_token` | string | `""` | Telegram secret token |
| `webhook_max_connections` | int | `4` | Max webhook connections |

---

## Discord

**File**: `channels/discord.py`

Discord bot using discord.py with slash command support.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable Discord channel |
| `token` | string | `""` | Bot token |
| `allow_channels` | list | `[]` | Allowed channel IDs |
| `intents` | int | `37377` | Discord gateway intents |
| `group_policy` | `"open"` \| `"mention"` | `"mention"` | Group response policy |
| `read_receipt_emoji` | string | `"👀"` | Read receipt emoji |
| `working_emoji` | string | `"🔧"` | Working indicator emoji |
| `working_emoji_delay` | float | `2.0` | Delay before showing working emoji |
| `streaming` | bool | `true` | Stream response updates |
| `proxy` | string \| null | `null` | Proxy URL |
| `username` | string \| null | `null` | Proxy username |
| `password` | string \| null | `null` | Proxy password |

---

## Slack

**File**: `channels/slack.py`

Slack bot using Socket Mode with Bolt.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable Slack channel |
| `bot_token` | string | `""` | Bot token (`xoxb-...`) |
| `app_token` | string | `""` | App-level token (`xapp-...`) |
| `reply_in_thread` | bool | `false` | Reply in threads |
| `react_emoji` | string | `"eyes"` | Reaction emoji |
| `done_emoji` | string | `"white_check_mark"` | Done reaction emoji |
| `include_thread_context` | bool | `false` | Include thread context in messages |
| `thread_context_limit` | int | `20` | Max messages for thread context |
| `group_policy` | `"open"` \| `"mention"` | `"mention"` | Group response policy |
| `group_allow_from` | list | `[]` | Allowed user IDs in groups |
| `group_require_mention` | bool | `true` | Require @mention in groups |

### Slack DM Config

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `dm.enabled` | bool | `true` | Enable DM conversations |
| `dm.policy` | `"open"` \| `"pairing"` | `"pairing"` | DM access policy |
| `dm.allow_from` | list | `[]` | Allowed user IDs |

---

## Signal

**File**: `channels/signal.py`

Signal messenger via signal-cli daemon.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable Signal channel |
| `phone_number` | string | `""` | Bot phone number |
| `daemon_host` | string | `"127.0.0.1"` | signal-cli daemon host |
| `daemon_port` | int | `8080` | signal-cli daemon port |
| `group_message_buffer_size` | int | `20` | Buffer size for group messages |

### Signal DM Config

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `dm.enabled` | bool | `true` | Enable DM conversations |
| `dm.policy` | `"open"` \| `"pairing"` | `"pairing"` | DM access policy |
| `dm.allow_from` | list | `[]` | Allowed user IDs |

### Signal Group Config

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `group.enabled` | bool | `false` | Enable group conversations |
| `group.policy` | `"open"` \| `"mention"` | `"mention"` | Group response policy |
| `group.allow_from` | list | `[]` | Allowed group IDs |

---

## WhatsApp

**File**: `channels/whatsapp.py`

WhatsApp via whatsapp-web.js bridge.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable WhatsApp channel |
| `database_path` | string | `""` | Path to whatsapp-web.js database |
| `lid_mappings` | dict | `{}` | LID to JID mappings |
| `group_policy` | `"open"` \| `"mention"` | `"mention"` | Group response policy |

---

## Matrix

**File**: `channels/matrix.py`

Matrix protocol with optional E2EE support.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable Matrix channel |
| `homeserver` | string | `""` | Homeserver URL |
| `user_id` | string | `""` | Bot user ID |
| `password` | string | `""` | Account password |
| `access_token` | string | `""` | Access token (alternative to password) |
| `device_id` | string | `""` | Device ID for E2EE |
| `e2ee_enabled` | bool | `true` | Enable end-to-end encryption |
| `sas_verification` | bool | `true` | Enable SAS verification |
| `max_media_bytes` | int | `52428800` | Max media upload size |
| `group_policy` | `"open"` \| `"mention"` \| `"restricted"` | `"mention"` | Group policy |
| `group_allow_from` | list | `[]` | Allowed room IDs |
| `allow_room_mentions` | bool | `true` | Allow room mention detection |
| `streaming` | bool | `false` | Stream response updates |

---

## Feishu (Lark)

**File**: `channels/feishu.py`

Feishu/Lark bot with topic isolation.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable Feishu channel |
| `app_id` | string | `""` | App ID |
| `app_secret` | string | `""` | App secret |
| `encrypt_key` | string | `""` | Event encryption key |
| `verification_token` | string | `""` | Verification token |
| `react_emoji` | string | `"THUMBSUP"` | Reaction emoji |
| `done_emoji` | string | `""` | Done reaction emoji |
| `tool_hint_prefix` | string | `"🔧"` | Tool hint prefix |
| `group_policy` | `"open"` \| `"mention"` | `"mention"` | Group policy |
| `streaming` | bool | `true` | Stream response updates |
| `domain` | `"feishu"` \| `"lark"` | `"feishu"` | Domain selection |
| `topic_isolation` | bool | `true` | Isolate conversations by topic |

---

## DingTalk

**File**: `channels/dingtalk.py`

DingTalk bot via Stream API.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable DingTalk channel |
| `client_id` | string | `""` | Client ID |
| `client_secret` | string | `""` | Client secret |
| `allow_remote_media_redirects` | bool | `false` | Allow remote media downloads |
| `remote_media_redirect_allowed_hosts` | list | `[]` | Allowed hosts for media |
| `group_user_isolation` | bool | `true` | Isolate users in groups |

---

## WeCom

**File**: `channels/wecom.py`

WeCom (Enterprise WeChat) bot.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable WeCom channel |
| `bot_id` | string | `""` | Bot ID |
| `secret` | string | `""` | Bot secret |
| `welcome_message` | string | `""` | Welcome message text |

---

## WeChat

**File**: `channels/weixin.py`

WeChat personal account integration.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable WeChat channel |
| `base_url` | string | `""` | WeChat bridge base URL |
| `cdn_base_url` | string | `""` | CDN base URL for media |
| `route_tag` | string | `""` | Route tag |
| `token` | string | `""` | Auth token |
| `state_dir` | string | `""` | State directory |
| `poll_timeout` | float | `35` | Poll timeout in seconds |

---

## MS Teams

**File**: `channels/msteams.py`

Microsoft Teams bot via Bot Framework.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable MS Teams channel |
| `app_id` | string | `""` | App registration ID |
| `app_password` | string | `""` | App password |
| `tenant_id` | string | `""` | Azure AD tenant ID |
| `host` | string | `"0.0.0.0"` | Bot server bind address |
| `port` | int | `3978` | Bot server port |
| `path` | string | `"/api/messages"` | Message endpoint path |
| `reply_in_thread` | bool | `false` | Reply in threads |
| `mention_only_response` | bool | `true` | Only respond when mentioned |
| `validate_inbound_auth` | bool | `true` | Validate inbound auth tokens |
| `ref_ttl_days` | int | `30` | Reference TTL in days |
| `trusted_service_url_hosts` | list | `[]` | Trusted service URL hosts |

---

## WebSocket

**File**: `channels/websocket.py`

WebSocket channel for the WebUI and programmatic access.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable WebSocket channel |
| `host` | string | `"127.0.0.1"` | Bind address |
| `port` | int | `8765` | WebSocket port |
| `unix_socket_path` | string \| null | `null` | Unix socket path |
| `path` | string | `"/ws"` | WebSocket path |
| `token` | string \| null | `null` | Auth token |
| `token_issue_path` | string | `"/auth/token"` | Token issuance endpoint |
| `token_secret` | string \| null | `null` | Token signing secret |
| `token_ttl_s` | int | `300` | Token TTL in seconds |
| `websocket_requires_token` | bool | `false` | Require token for WS connection |
| `allow_from` | list | `["*"]` | Allowed user IDs |
| `max_message_bytes` | int | `41943040` | Max message size (40MB) |
| `ping_interval` | int | `30` | WebSocket ping interval |
| `ping_timeout` | int | `10` | WebSocket ping timeout |
| `ssl_certfile` | string \| null | `null` | SSL certificate path |
| `ssl_keyfile` | string \| null | `null` | SSL key path |

---

## Email

**File**: `channels/email.py`

Email channel with IMAP polling and SMTP responses.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable Email channel |
| `consent_granted` | bool | `false` | User consent for email processing |
| `imap_host` | string | `""` | IMAP server host |
| `imap_port` | int | `993` | IMAP port |
| `imap_username` | string | `""` | IMAP username |
| `imap_password` | string | `""` | IMAP password |
| `imap_mailbox` | string | `"INBOX"` | Mailbox to monitor |
| `imap_use_ssl` | bool | `true` | Use SSL for IMAP |
| `smtp_host` | string | `""` | SMTP server host |
| `smtp_port` | int | `587` | SMTP port |
| `smtp_username` | string | `""` | SMTP username |
| `smtp_password` | string | `""` | SMTP password |
| `smtp_use_tls` | bool | `true` | Use TLS for SMTP |
| `smtp_use_ssl` | bool | `false` | Use SSL for SMTP |
| `from_address` | string | `""` | Sender email address |
| `auto_reply_enabled` | bool | `false` | Auto-reply to incoming emails |
| `poll_interval_seconds` | int | `30` | IMAP poll interval |
| `post_action` | `"delete"` \| `"move"` | `"delete"` | Action after processing |
| `max_body_chars` | int | `12000` | Max email body chars |
| `subject_prefix` | string | `"Re: "` | Reply subject prefix |
| `verify_dkim` | bool | `true` | Verify DKIM signatures |
| `verify_spf` | bool | `true` | Verify SPF records |
| `allowed_attachment_types` | list | `[".txt", ".pdf", ...]` | Allowed attachment MIME types |
| `max_attachment_size` | int | `10485760` | Max attachment size (10MB) |
| `max_attachments_per_email` | int | `5` | Max attachments per email |

---

## QQ

**File**: `channels/qq.py`

QQ bot via QQ Bot API.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable QQ channel |
| `app_id` | string | `""` | App ID |
| `secret` | string | `""` | App secret |
| `msg_format` | `"plain"` \| `"markdown"` | `"plain"` | Message format |
| `ack_message` | string | `"⏳ Processing..."` | Acknowledgment message |
| `media_dir` | string | `""` | Media storage directory |
| `download_chunk_size` | int | `8192` | Download chunk size |
| `download_max_bytes` | int | `209715200` | Max download size (200MB) |

---

## Napcat

**File**: `channels/napcat.py`

Napcat (QQ) bot via WebSocket.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable Napcat channel |
| `ws_url` | string | `""` | WebSocket URL |
| `access_token` | string | `""` | Access token |
| `group_policy` | `"mention"` \| `"open"` \| float (0-1) | `"mention"` | Group policy (float = mention probability) |
| `group_policy_overrides` | dict | `{}` | Per-group policy overrides |
| `welcome_new_members` | bool | `false` | Welcome new group members |
| `max_image_bytes` | int | `20971520` | Max image size (20MB) |

---

## Mochat

**File**: `channels/mochat.py`

Mochat integration with per-group rules.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable Mochat channel |
| `base_url` | string | `""` | API base URL |
| `socket_url` | string | `""` | WebSocket URL |
| `socket_path` | string | `"/ws"` | WebSocket path |
| `socket_disable_msgpack` | bool | `false` | Disable msgpack encoding |
| `socket_reconnect` | bool | `true` | Auto-reconnect |
| `socket_max_reconnect_delay_ms` | int | `30000` | Max reconnect delay |
| `refresh_interval_ms` | int | `5000` | Refresh interval |
| `watch_timeout_ms` | int | `10000` | Watch timeout |
| `watch_limit` | int | `100` | Watch limit |
| `claw_token` | string | `""` | Auth token |
| `agent_user_id` | string | `""` | Agent user ID |
| `sessions` | dict | `{}` | Session configuration |
| `panels` | dict | `{}` | Panel configuration |
| `reply_delay_mode` | string | `""` | Reply delay mode |
| `reply_delay_ms` | int | `0` | Reply delay in ms |

### Mochat Group Rules

```json
{
  "groups": {
    "group_id": {
      "require_mention": true,
      "session_key": "custom:session"
    }
  }
}
```
