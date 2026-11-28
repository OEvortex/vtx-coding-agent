# Tools

The built-in tools vx gives the model, from `DEFAULT_TOOLS` in `packages/vx/src/vx/tools/__init__.py`. `/tools` lists whatever is loaded, built-ins first and then one section per MCP server as `server > tool`.

| Tool | What it does |
|---|---|
| `read` | Read files (with image support) and list directories |
| `apply_patch` | Edit files: replace / insert / create ops on exact text |
| `bash` | Run shell commands; `wait_ms` and `background=true` promote long calls to jobs with `status`/`list`/`send_input`/`kill` actions |
| `web` | `mode="search"` queries the web; `mode="fetch"` reads a URL as markdown |
| `search` | Find text or files — ripgrep, falling back through `git grep` to a Python walk, so it always answers |
| `skill` | Load a skill's `SKILL.md` body into context by name |
| `ask_user` | Stop mid-turn and ask the user 1-4 questions with options |
| `todo` | Publish the plan the sidebar draws |
| `delegate_subagent` | Run one isolated agent for a self-contained side task; reports once and is gone |
| `swarm` | Create and steer a swarm of parallel workers (see [swarm.md](swarm.md)) |
| `board` | Post to, read, or wait on the swarm's shared board |

## Parent-only tools

`swarm`, `board`, and `delegate_subagent` are registered `parent_only` (`PARENT_ONLY` in `tools/__init__.py`): the top-level conversation gets them, but an agent vx dispatches — a `delegate_subagent` helper or a swarm worker — does not, so helpers cannot start their own swarm or recurse. Swarm workers still get the ordinary coding tools.

Tools from MCP servers (`/mcp`) and from agents and extensions are added on top of this list at runtime; none of them are built in.
