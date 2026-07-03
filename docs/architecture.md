# Architecture

This doc is the contributor's map of Vtx. It's intentionally cross-references-heavy — the goal is for you to find the right file fast, not to summarize the whole codebase in prose.

## High-level pipeline

```text
                  ┌────────────────┐
                  │     CLI/cli.py │
                  │   (argparse)   │
                  └───────┬────────┘
                          │
            ┌─────────────┴─────────────┐
            │                           │
   ┌────────▼─────────┐       ┌─────────▼─────────┐
   │  -p / --prompt   │       │  default (TUI)    │
   │  headless.py     │       │  ui/launch.py     │
   └────────┬─────────┘       └─────────┬─────────┘
            │                           │
            └─────────────┬─────────────┘
                          │
                  ┌───────▼────────┐
                  │   runtime.py   │
                  │ ConversationRuntime │
                  └───────┬────────┘
                          │
              ┌───────────┴───────────┐
              │                       │
       ┌──────▼──────┐        ┌───────▼──────┐
       │  loop.py    │        │   llm/       │
       │  agent loop │◄──────►│  providers/  │
       └──────┬──────┘        └──────────────┘
              │
       ┌──────▼──────┐        ┌──────────────┐
       │  tools/     │        │  events.py   │
       │  registry   │───────►│  AgentEvents │
       └─────────────┘        └──────────────┘
```

The TUI consumes `AgentEvents` to render. The headless runner consumes the same event stream and prints a text transcript. Both are thin wrappers around the same `ConversationRuntime` / `AgentLoop`.

## Directory map

```text
src/vtx/
├── cli.py                 # argparse: --model, --provider, --prompt, --resume, --agent, etc.
├── headless.py            # non-interactive runner; reads --prompt, prints to stdout
├── version.py             # __version__ (semver, used by update_check and the build)
├── update_check.py        # PyPI version probe for the in-app update notice
│
├── agents/                # switchable handoff agents (``.vtx/agent/<name>.py``)
│   ├── schema.py          # Pydantic model: AgentDef, PermissionGate
│   ├── discovery.py       # project + global discovery
│   ├── loader.py          # import + AGENT validation + register(api) call
│   ├── api.py             # AgentAPI: local_tool, local_command, permission_gate, on
│   ├── registry.py        # AgentRegistry: active agent state + cycle
│   └── activate.py        # compose_active_tools, compose_active_commands
│
├── goal.py                # /goal command: Goal, GoalManager, evaluator prompt,
│                           # budget-clause parsing, persistence helpers
│
├── config.py              # Pydantic config schema, migration, getters/setters
├── themes.py              # built-in theme registry + ColorsConfig
├── session.py             # JSONL session class, list/load/save, info/totals
├── permissions.py         # mode + safe-command allowlist + decision algorithm
├── notify.py              # audio notification (WAV via aplay/PowerShell)
├── tools_manager.py       # dynamic tool registry helpers (rarely used)
│
├── core/                  # framework-agnostic types and pure functions
│   ├── types.py           # Message, UserMessage, AssistantMessage, ToolCall, etc.
│   ├── errors.py
│   ├── compaction.py      # is_overflow + generate_summary (LLM-driven)
│   └── handoff.py         # generate_handoff_prompt (LLM-driven)
│
├── context/               # discovery: AGENTS.md, skills, git status
│   ├── agent_mds.py       # walk to git root, load AGENTS.md/CLAUDE.md
│   ├── skills.py          # load + validate + render skills
│   ├── git.py             # git status snapshot
│   ├── loader.py          # Context.load(cwd) — bundles the above
│   └── _xml.py            # XML escape helpers
│
├── prompts/               # system prompt assembly
│   ├── builder.py         # build_system_prompt — composes sections
│   ├── identity.py        # DEFAULT_VTX_BASE — named sections
│   ├── tooling.py         # aggregates tool prompt_guidelines
│   └── env.py             # # Env section (cwd, OS, Python, vtx version)
│
├── tools/                 # the 5 built-in tools
│   ├── base.py            # BaseTool, ToolResult
│   ├── bash.py
│   ├── edit.py
│   ├── find.py
│   ├── read.py
│   ├── write.py
│   ├── _read_image.py     # image decode/resize for read
│   └── _tool_utils.py     # cancellation, truncation, path shortening
│
├── llm/                   # provider framework
│   ├── base.py            # BaseProvider, LLMStream, ProviderConfig
│   ├── models.py          # static Model catalog (MODELS dict)
│   ├── dynamic_models.py  # DYNAMIC_PROVIDERS + caching
│   └── providers/
│       ├── __init__.py    # PROVIDER_API_BY_NAME, factory
│       ├── openai_completions.py
│       ├── openai_responses.py
│       ├── openai_codex_responses.py
│       ├── anthropic.py
│       ├── copilot.py              # GitHub Copilot transport
│       ├── copilot_anthropic.py    # Copilot's Anthropic variant
│       ├── azure_ai_foundry.py
│       ├── github_copilot_headers.py
│       ├── openai_compat.py        # OpenAI-compatible generic transport
│       ├── sanitize.py             # response sanitization
│       └── mock.py                 # for tests
│
├── llm/oauth/             # OAuth flows
│   ├── openai.py          # OpenAI Codex (PKCE)
│   ├── copilot.py         # GitHub Copilot (device flow / gh reuse)
│   └── dynamic.py         # dynamic provider API key storage
│
├── ui/                    # Textual TUI
│   ├── launch.py          # run_tui() — entry point
│   ├── app.py             # the Textual App
│   ├── app_protocol.py    # interface the app implements for testability
│   ├── agent_runner.py    # wires the runtime + event stream to the TUI
│   ├── chat.py            # chat log widget
│   ├── blocks.py          # tool / message / thinking blocks
│   ├── export.py          # /export → standalone HTML
│   ├── tree.py            # /tree session tree selector
│   ├── input.py           # the input box (multi-line, history, paste)
│   ├── completion_ui.py   # @file / /cmd / !cmd autocomplete
│   ├── autocomplete.py    # completion data sources
│   ├── floating_list.py   # generic floating picker widget
│   ├── widgets.py         # misc widgets
│   ├── styles.py          # CSS class builders from theme
│   ├── formatting.py      # text utilities
│   ├── prompt_history.py
│   ├── path_complete.py
│   ├── startup.py
│   ├── welcome.py
│   ├── launch.py
│   ├── queue_ui.py        # queued prompts + steer queue display
│   ├── session_ui.py      # /session info modal
│   ├── tool_output.py     # tool output rendering
│   ├── clipboard.py
│   ├── latex.py           # LaTeX → Unicode rendering
│   ├── selection_mode.py  # text selection within the TUI
│   └── commands/          # slash-command mixins
│       ├── base.py        # CommandSupport mixin
│       ├── settings.py    # /themes, /permissions, /thinking, /notifications
│       ├── models.py      # /model
│       ├── sessions.py    # /new, /resume, /tree, /session, /handoff, /compact, /export, /copy, /clear
│       └── auth.py        # /login, /logout
│
├── defaults/
│   └── config.yml         # shipped config schema; copied to ~/.vtx/ on first run
│
├── builtin_skills/        # shipped slash-command skills
│   ├── init/SKILL.md      # /init
│   ├── review/SKILL.md    # /review
│   └── skill-builder/SKILL.md  # /skill-builder
│
└── runtime.py             # ConversationRuntime — the per-session object
```

## Core types (`core/types.py`)

The wire format. Everything else is a renderer or a producer.

```python
class UserMessage(BaseModel):
    role: Literal["user"]
    content: list[TextContent | ImageContent]


class AssistantMessage(BaseModel):
    role: Literal["assistant"]
    content: list[TextContent | ThinkingContent | ToolCall]
    stop_reason: StopReason  # STOP | LENGTH | ERROR
    usage: Usage  # tokens
    model: str


class ToolResultMessage(BaseModel):
    role: Literal["tool_result"]
    tool_call_id: str
    content: list[TextContent | ImageContent]
    is_error: bool


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]
```

These are the only message shapes that flow between the LLM provider and the agent loop. Tool inputs are validated against each tool's Pydantic `params` model before the tool runs; tool outputs are wrapped in `ToolResultMessage` with `is_error` set when the tool raised.

## Conversation runtime

`ConversationRuntime` (in `runtime.py`) is the per-session object. It owns:

- The active `BaseProvider` instance.
- The active tool list.
- The session message history (in-memory mirror of the JSONL file).
- The current thinking level, model, and provider.
- The auth mode for OpenAI-/Anthropic-compatible endpoints.

`runtime.prepare_for_run()` returns an `AgentLoop` configured for the current settings. The loop is the consumer/producer of `AgentEvent`s.

## Agent loops

Vtx ships two execution engines. Both consume the same core types (`Message`, `AssistantMessage`, `ToolResultMessage`) but differ in scope, resilience, and concurrency.

### Native event loop (`vtx/loop.py` + `vtx/turn.py`)

The **native event loop** powers the TUI and headless CLI (`vtx -p "..."`).

```
src/vtx/
├── loop.py   # Agent — async generator yielding typed Events
└── turn.py   # _TurnRunner / run_single_turn — single-turn stream + tool execution
```

Turn shape:
1. Yield `TurnStartEvent`.
2. Call `run_single_turn()` → stream LLM parts → yield `TextDeltaEvent`, `ThinkingDeltaEvent`, `ToolStartEvent`, `ToolArgsDeltaEvent`, `ToolEndEvent`, `ToolResultEvent`.
3. Yield `TurnEndEvent` with the final `AssistantMessage`.
4. If the model returned tool calls, the outer `Agent.run()` loop continues to the next turn.

Key properties:
- **Event stream**: the TUI is a pure consumer of `AgentEvent`s; the headless runner is a different consumer of the same stream.
- **Cancellation**: races each stream chunk against `cancel_event` via `asyncio.wait(FIRST_COMPLETED)` so ESC takes effect immediately.
- **Permissions**: auto-approve read-only tools, prompt for mutating ones, and yield `ToolApprovalEvent` for the UI to resolve.
- **Stall detection**: tool-call idle timeout (`tool_call_idle_timeout_seconds`) finalizes partial arguments instead of hanging.
- **Compaction**: after each turn, if the most recent assistant usage exceeds the context window threshold, `generate_summary()` produces a synthetic compaction message and the loop continues with the summary in place.

### Advanced backend loop (`vtx_claw/agent/loop.py` + `vtx_claw/agent/runner.py`)

The **advanced backend loop** is part of the same `vtx-coding-agent` package. It powers the `vtx-claw gateway` command and the WebUI. It is a state-machine-driven, multi-session, concurrent executor.

```
src/vtx_claw/agent/
├── loop.py     # AgentLoop — 8-state machine (RESTORE → COMPACT → ... → DONE)
└── runner.py   # AgentRunner — inner LLM iteration with streaming + tool execution
```

Turn shape (inside `AgentLoop._state_run`):
1. `_run_agent_loop()` creates an `AgentProgressHook` (wraps streaming/progress callbacks) and composes it with any extra `AgentHook`s via `CompositeHook`.
2. `AgentRunner.run()` iterates up to `max_iterations`:
   - Call `_request_model()` → consume `TextPart`, `ThinkPart`, `ToolCallStart`, `ToolCallDelta`, `StreamDone` from a bridge adapter.
   - If tools are present, execute them in concurrent batches (`asyncio.gather` for `concurrency_safe` tools).
   - Empty-content retry (2×), length recovery (3×), malformed-tool-call retry, and injection draining between iterations.
3. Return `(final_content, tools_used, messages, stop_reason)` to `_state_save`, then `_state_respond`.

Key properties:
- **State machine**: explicit `TurnState` enum with a `_TRANSITIONS` table and per-state trace logging.
- **Concurrency**: per-session locks, cross-session semaphore (`VTX_CLAW_MAX_CONCURRENT_REQUESTS`), and concurrent tool batching.
- **Resilience**: context governance (orphan-tool-result repair, block limits), runtime checkpoints for `/stop` recovery, error-isolated `CompositeHook`.
- **Injection system**: mid-turn follow-up messages drained from a per-session pending queue, capped at `_MAX_INJECTIONS_PER_TURN`.
- **Subagents**: `SubagentManager` spawns isolated child sessions and streams live progress back into the parent.
- **Memory**: `Consolidator` summarizes old turns in the background; `AutoCompact` runs pre-turn compaction.

### When each is used

The native event loop is in the base install. The advanced `vtx_claw` backend requires the `[claw]` extra because it includes channel adapters, cron, MCP, and subagent dependencies.

| Entry point | Loop used | Notes |
|---|---|---|
| `vtx` (TUI) | Native event loop | Interactive Textual session |
| `vtx -p "..."` (headless) | Native event loop | Non-interactive stdout runner |
| `vtx-claw gateway` | Advanced backend loop | Persistent multi-channel + WebUI server |

1. Send the message list to the provider.
2. Receive a stream of `LLMStream` parts.
3. Accumulate an `AssistantMessage` (text + thinking + tool calls).
4. For each tool call, validate the arguments, run `check_permission`, either execute or render an approval prompt, then wrap the result in a `ToolResultMessage`.
5. Append the new messages to the session JSONL.
6. Emit `AgentEvent`s (text deltas, thinking deltas, tool call, tool result, agent end).
7. If the assistant message had no tool calls (or stop reason is STOP/ERROR), exit. Otherwise go to 1.

The loop respects the `cancel_event` from the TUI: pressing `Esc` flips the event, and the loop tears down the in-flight tool call, terminates the provider stream, and exits with `StopReason.STOP`.

## Providers

`BaseProvider` is a thin ABC:

```python
class BaseProvider(ABC):
    async def stream(self, messages, *, system_prompt, tools) -> AsyncIterator[StreamPart]: ...
    async def count_tokens(self, messages) -> int: ...
```

The `stream` method is the only thing providers must implement. `count_tokens` is best-effort — most providers don't expose it, so the loop falls back to a heuristic for compaction math.

Each concrete provider:

- Holds a `httpx.AsyncClient` (or `aiohttp.ClientSession`) with the right headers.
- Translates the Vtx `Message` format into the provider's wire format.
- Streams the response, yielding `TextPart` / `ThinkingPart` / `ToolCallPart` as they arrive.
- Sanitizes the response (drop empty messages, drop unsigned thinking blocks for Anthropic).

The dynamic catalog providers (`airouter`, `opencode`, `kilo`, `tokenrouter`) all use the same OpenAI-completions transport, with different `base_url` and headers.

## Tools

`BaseTool` (in `tools/base.py`):

```python
class BaseTool(BaseModel, ABC):
    name: str
    description: str
    tool_icon: str
    mutating: bool
    params: type[BaseModel]
    prompt_guidelines: tuple[str, ...] = ()

    async def execute(self, params, cancel_event=None) -> ToolResult: ...
    def format_call(self, params) -> str: ...
    def format_preview(self, params) -> str | None: ...
```

The Pydantic `params` class is reused as the JSON schema for the LLM. `prompt_guidelines` lines are aggregated into the `# Tool usage` section of the system prompt. `format_call` is the one-line call summary in the tool block; `format_preview` is the longer preview shown only during the approval modal.

Cancellation: `execute` is responsible for honoring the `cancel_event` (or wrapping a subprocess with `communicate_or_cancel` from `_tool_utils`).

## Context loading

`context/loader.py::Context.load(cwd)` is the single entry point. It returns a `Context` bundle with:

- `agents_files`: the list of `AGENTS.md` / `CLAUDE.md` files, closest-last.
- `skills`: the discovered skills.
- `git_status`: a snapshot of `git status` (or `None` if disabled / not a git repo).
- `project_root`: the cwd's git root (or the cwd if not in a repo).

The system prompt builder joins this with the base identity, the tool guidelines, the git section, and the env section. See `prompts/builder.py`.

## TUI

The Textual app is a single big widget tree, broken into:

- `ChatLog` (`chat.py`) — the scrollable message history.
- `InputBox` (`input.py`) — the multi-line input with history, paste, autocomplete.
- `InfoBar` (in `app.py`) — the bottom status bar (provider, model, thinking, permission mode, file-change count).
- Floating widgets — pickers (`/model`, `/themes`, `/resume`, etc.) and the approval modal.
- A command router (`ui/commands/__init__.py`) that dispatches `/` slash commands to the right mixin.

The TUI is **not** on the request path. It only consumes `AgentEvent`s and renders. This separation is what makes the headless mode possible — the same loop with a different renderer.

## Event flow

`events.py` defines the `AgentEvent` hierarchy:

```python
class AgentEvent: ...
class AgentStartEvent(AgentEvent): ...
class TurnStartEvent(AgentEvent): ...
class TextDeltaEvent(AgentEvent): text: str ...
class ThinkingDeltaEvent(AgentEvent): text: str ...
class ToolCallEvent(AgentEvent): tool_call: ToolCall ...
class ToolResultEvent(AgentEvent): result: ToolResultMessage ...
class ToolApprovalEvent(AgentEvent): tool_name: str; future: Future ...
class TurnEndEvent(AgentEvent): assistant_message: AssistantMessage ...
class AgentEndEvent(AgentEvent): stop_reason: StopReason ...
class ErrorEvent(AgentEvent): error: str ...
class FileChangeEvent(AgentEvent): path: str; kind: str ...
class InfoEvent(AgentEvent): message: str ...
```

The Textual app subscribes to these and updates widgets. The headless renderer subscribes to the same stream and prints a text transcript.

## Decisions worth knowing

A few design choices that aren't obvious from the code:

- **System prompt composition is in Python, not YAML.** The default base identity lives in `prompts/identity.py` and is composed from named sections. The shipped `defaults/config.yml` keeps an empty placeholder for `llm.system_prompt.content`; the loader fills it in from Python. This lets us edit the prompt in code, test it, and version it without a YAML roundtrip.
- **Config migration is one-way and automatic.** There is no in-app "revert to old config" button. The backup file is the recovery path.
- **Tools are all Pydantic-typed.** Arguments are validated against the tool's `params` model before the tool runs. The Pydantic JSON schema is sent to the LLM as the tool definition.
- **Permissions are a pure function.** `check_permission(tool, arguments) -> PermissionDecision` is a single function with no UI dependency. The TUI just calls it and renders a popup on `PROMPT`. This is why headless mode is safe — there's no path that asks for approval.
- **Sessions are append-only JSONL.** No rewrite, no compaction in place. The "compaction" feature only changes what the model sees on resume; the full history is always on disk.
- **Dynamic provider catalogs are cached with a TTL.** A network blip doesn't break the picker — you just see the cached list with stale data. The first lookup after a network blip triggers a refresh.
- **OAuth tokens are stored in `~/.vtx/` with mode 0600.** Not in the keychain (yet). Cross-platform file permissions are best-effort.

## Where to start reading

- **Adding a tool:** read `tools/base.py` and `tools/read.py` (the simplest one), then add your tool to `tools/__init__.py`.
- **Adding a provider:** read `llm/providers/openai_completions.py` (the smallest one) and `llm/dynamic_models.py` (for the catalog-fetch path). Register in `llm/providers/__init__.py`.
- **Adding a slash command:** pick the right mixin in `ui/commands/`, follow the pattern in `settings.py` (the cleanest one).
- **Adding a handoff agent:** read [docs/agents.md](agents.md), then `src/vtx/agents/schema.py` and `src/vtx/agents/loader.py`. The loader is small (under 150 lines) and mirrors `extensions.py`.
- **Tuning the system prompt:** edit `prompts/identity.py`. Add a new named section and reference it in `prompts/builder.py`.
- **Changing the schema:** bump `config_version` in `defaults/config.yml`, add a `_migrate_vN_to_vN+1` in `config.py`, register it in `_migrate_config_data`.

For the dev loop, see [development.md](development.md).
