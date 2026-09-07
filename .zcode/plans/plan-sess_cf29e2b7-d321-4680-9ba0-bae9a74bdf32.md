
# Implementation Plan: RLM Mode for VTX

## Goal
Add an **RLM mode** toggle to VTX that switches the runtime into a REPL-first, Prime-Agent-like behavior, plus a Prime-Agent-inspired TUI experience implemented as `vtx.tui.rlm` within the existing Textual app.

---

## Phase 1 — RLM Mode Configuration & Master Prompt

### 1.1 Add mode to config schema
- **File:** `src/vtx/coding_agent/config.py`
- Add `mode: "tool_first" | "rlm"` to the user YAML schema, defaulting to `"tool_first"`.
- Add a migration so existing configs without `mode` continue to load.

### 1.2 Create the RLM master system prompt
- **New file:** `src/vtx/coding_agent/prompts/rlm.py`
- Build a **~1,200-token** system prompt that redefines the agent’s behavior for RLM mode:
  - The single primary tool is `python_repl`; all work happens through it.
  - Skills are described as **REPL commands** the user can invoke (`/skill:name` expands into a REPL snippet).
  - Sub-agents are dispatched via `rlm(...)` function calls inside the REPL.
  - Goals are tracked via `goal_set(...)` / `goal_checkpoint(...)` REPL helpers.
  - The prompt explicitly tells the model: *“If the user asks for anything outside the REPL, execute a Python snippet that performs it.”*
- Wire it into `src/vtx/coding_agent/prompts/builder.py` so that when `mode == "rlm"`, the base prompt section is replaced with the RLM prompt.

### 1.3 Master prompt for users (the artifact)
- **New file:** `src/vtx/coding_agent/prompts/rlm_master_prompt.md`
- A user-facing markdown prompt that, when loaded as a skill or injected at session start, teaches the model to behave like Prime Agent inside VTX. This is the “master prompt” the user asked for.
- It documents:
  - The `python_repl` tool contract
  - Available REPL helpers (`rlm`, `goal_set`, `goal_checkpoint`, `read_file`, `write_file`, `run_bash`)
  - Skill invocation pattern
  - Sub-agent orchestration pattern
  - Error recovery and compaction behavior

---

## Phase 2 — The `python_repl` Tool

### 2.1 New tool module
- **New file:** `src/vtx/ai/agent/tools/repl.py`
- `ReplTool(BaseTool)` with params: `code: str`, `session_id?: str`.
- **Subprocess manager** (new `src/vtx/ai/agent/repl_manager.py`):
  - Maintains a long-lived `python -i` subprocess per session.
  - Sends code snippets separated by `\n`.
  - Streams stdout/stderr back via `on_output` → `ToolOutputDeltaEvent` (already wired in `turn.py`).
  - Handles image display (base64 → PNG temp file → prints ANSI image protocol or file path).
  - Detects REPL restarts, timeouts, and kernel crashes.

### 2.2 Wire into the harness
- Register `ReplTool` in `src/vtx/ai/agent/tools/__init__.py` as a default tool.
- In RLM mode, the active tool list collapses to: `python_repl`, `skill`, `goal` (and `web` if configured). The surgical `read`/`edit`/`write`/`bash`/`find` tools are **still available** but downplayed in the prompt.

---

## Phase 3 — RLM TUI Screen (`vtx.tui.rlm`)

### 3.1 New module
- **New directory:** `src/vtx/tui/rlm/`
  - `__init__.py`
  - `screen.py` — `RlmScreen(Screen)` — the main RLM experience
  - `editor.py` — `RlmEditor(TextArea)` — Prime-Agent-style multi-line editor with slash-command/file-path autocomplete
  - `blocks.py` — `RlmThinkingBlock`, `RlmContentBlock`, `RlmToolBlock` — streaming markdown/streaming-aware renderers
  - `status.py` — Kernel status indicator, model badge, context usage

### 3.2 Layout
Prime Agent’s TUI is editor-centric. In Textual terms:

```
┌──────────────────────────────────────┐
│ Header: vtx · RLM · claude-sonnet    │  ← StatusLine
├──────────────────────────────────────┤
│                                      │
│  Chat log (differential refresh)     │  ← ChatLog-like VerticalScroll
│  - streaming thinking                │
│  - markdown assistant messages       │
│  - REPL tool output (syntax color)   │
│  - skill/goal notifications          │
│                                      │
├──────────────────────────────────────┤
│ Footer: model · thinking · tokens    │  ← InfoBar
├──────────────────────────────────────┤
│ Editor + autocomplete overlay        │  ← RlmEditor + FloatingList
└──────────────────────────────────────┘
```

### 3.3 Prime-Agent TUI features to clone
| Feature | Prime Agent | VTX RLM implementation |
|---------|-------------|------------------------|
| Differential rendering | CSI 2026, line diffing | Textual `refresh()` with `repaint=True` on changed blocks only; cache closed blocks via `_StreamingMarkdownMixin` pattern |
| Editor paste markers | `[paste #1 +42 lines]` | Reuse VTX’s existing paste handling in `input.py`; apply to `RlmEditor` |
| Overlay autocomplete | Positioned above editor | `FloaytingList` with `aboveMarker` positioning, already used in VTX |
| Thinking collapse | Ctrl+T | Reuse existing thinking-level backgrounds + collapse bindings |
| Image display | Kitty/iTerm2 protocol + metadata fallback | Reuse VTX’s `_read_image.py` and `resize_image` pipeline |
| Status line | Spinner + witty lines | Reuse `StatusLine` mixin |

### 3.4 Mode switching
- `/mode rlm` and `/mode tool_first` slash commands.
- The app swaps the central container: in RLM mode it mounts `RlmScreen`; in tool-first it stays on the default `Vtx` screen.
- Runtime re-initializes with the new prompt + active tool list.

---

## Phase 4 — Skills-as-REPL Commands

### 4.1 Skill registration for REPL
- In `src/vtx/coding_agent/tools/skill.py`, add `ReplSkillParams` that, when `action="run"`, sends the SKILL.md body as a Python code snippet to `python_repl`.
- The RLM system prompt tells the model: *“To invoke skill X, call `python_repl` with the skill’s instructions pasted as a Python comment block, then execute the workflow.”*
- This mirrors Prime Agent’s model where skills are importable Python packages that the REPL can `import`.

### 4.2 Skill-trigger slash commands
- Reuse VTX’s existing `register_cmd` skill frontmatter.
- In RLM mode, `/skill:name` expands into a pre-filled REPL snippet in `RlmEditor`.

---

## Phase 5 — Testing & Polish

### 5.1 Tests
- **New:** `tests/test_rlm_mode.py` — mode switching, REPL tool streaming, session persistence in RLM mode, skill invocation in RLM mode.
- **New:** `tests/tui/test_rlm_screen.py` — screen composition, editor mount, autocomplete wiring, mode toggle.

### 5.2 Formatting/Lint
- `uv run ruff format .`
- `uv run ruff check .`
- `uvx ty check .`

---

## Files Changed Summary

| File | Action |
|------|--------|
| `src/vtx/coding_agent/config.py` | Modify |
| `src/vtx/coding_agent/prompts/builder.py` | Modify |
| `src/vtx/ai/agent/tools/__init__.py` | Modify |
| `src/vtx/ai/agent/tools/repl.py` | **New** |
| `src/vtx/ai/agent/repl_manager.py` | **New** |
| `src/vtx/coding_agent/prompts/rlm.py` | **New** |
| `src/vtx/coding_agent/prompts/rlm_master_prompt.md` | **New** |
| `src/vtx/tui/rlm/__init__.py` | **New** |
| `src/vtx/tui/rlm/screen.py` | **New** |
| `src/vtx/tui/rlm/editor.py` | **New** |
| `src/vtx/tui/rlm/blocks.py` | **New** |
| `src/vtx/tui/rlm/status.py` | **New** |
| `src/vtx/tui/commands/settings.py` | Modify (add `/mode`) |
| `tests/test_rlm_mode.py` | **New** |
| `tests/tui/test_rlm_screen.py` | **New** |

## Scope Boundaries
- **No daemon/supervisor** — VTX remains single-process; background work uses existing `BackgroundTaskManager`.
- **No TypeScript** — all in Python/Textual.
- **No breaking changes** — default mode remains `tool_first`; RLM is opt-in.
