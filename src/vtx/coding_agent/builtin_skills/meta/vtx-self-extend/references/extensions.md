# Extensions (ExtensionAPI) for Vtx

Use this surface only for a provider adapter or a message/entry renderer. For tools,
commands, event hooks, permission rules, and prompt sections, use a plugin row instead -
see `plugin-rows.md`.

## The API surface

`src/vtx/plugins/api.py:429` onward. The registration methods:

| Method | Purpose |
|---|---|
| `register_tool` | Add a tool to the active tool set |
| `register_local_tool` | Add a tool without exposing it to the model |
| `register_command` | Add a slash command |
| `register_provider` | Register an LLM provider adapter |
| `unregister_provider` | Remove a provider adapter |
| `register_message_renderer` | Render a custom session message type |
| `register_entry_renderer` | Render a custom session entry type |
| `register_flag` / `get_flag` | Read a user-set feature flag |
| `register_shortcut` | Bind a keyboard shortcut |
| `notify` / `ui` / `send_message` / `append_entry` | Reach the user mid-turn |
| `exec` / `get_all_tools` / `set_active_tools` / `set_model` / `set_thinking_level` | Act on the running session |

## Be careful here - a dead-code finding applies

Every `on_*` method on `ExtensionAPI` has **zero internal callers**:
`on_session_start`, `on_session_end`, `on_agent_start`, `on_agent_end`, `on_turn_start`,
`on_turn_end`, `on_tool_call`, `on_tool_result`, `on_user_bash`, `on_input`,
`on_before_agent_start`, `on_agent_activated`, `on_agent_changed`,
`on_tool_group_changed`, `on_model_select`, `on_thinking_level_select`. This was verified
by cross-referencing the whole repo, not by reading the file.

That does not mean they are broken - it means nothing in this repo exercises them, so a
bug there will not be caught by any test here. If you subscribe to an event, reach for
`ctx.on(...)` in a plugin row instead: that is the live `EventHub` the built-ins use
(`src/vtx/plugins/builtins/hooks.py:114`).

Likewise `unregister_provider`, `register_flag`, and `register_shortcut` are unreferenced
internally. Treat any of them as unverified until you have run it.

## The old extension system is gone

`src/vtx/agent/extension_manager.py` and `src/vtx/agent/extensions.py` have been deleted.
Do not import from them, and do not follow older documentation that references
`vtx.extensions` or an extension manager in the agent package. The live path is
`vtx.plugins.extensions` (`ExtensionService`, `load_for_runtime`, `reload_extensions`).

`vtx/plugins/extensions.py: load_extension` and `load_all_extensions` are superseded by
`load_for_runtime` and are reachable only from tests - do not build new code on them.

## Verification

After touching an extension, `/reload` reloads extensions and clears the extension module
cache (`src/vtx/tui/commands/reload.py:71-105`). It is refused while a turn is running -
wait for the turn to finish or interrupt it first.
