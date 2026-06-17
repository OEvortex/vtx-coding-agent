import type { BlogPost } from "../../types";

const post: BlogPost = {
    id: "mcp-extensions",
    title: "Self-Extensibility: The Python Extension API",
    excerpt:
        "Building custom capabilities with Vtx's Extension system, subscribing to agent events, and registering slash commands.",
    category: "Extensions",
    date: "May 8, 2026",
    readTime: "6 Min Read",
    content: `A developer's environment is highly unique. Rather than constraining users to static capabilities, Vtx is designed from the ground up to be self-extensible. By placing a single Python file in \`~/.vtx/agent/extensions/\` or passing the \`--extension\` CLI flag, developers can introduce custom tools, intercept operations, and listen to lifecycle events.

### The Extension API

Every extension exposes a top-level \`register(api)\` function. The \`ExtensionAPI\` enables:

- **Custom Tool Registration**: Register any Pydantic-model-backed class inheriting from \`BaseTool\`.
- **Slash Commands**: Register custom slash commands (like \`/test\` or \`/deploy\`) to execute python-driven steps.
- **Event Subscriptions**: Subscribe to lifecycle event hooks.

### Lifecycle Events & Interception

Vtx fires hooks through an internal event bus:

| Event | Blocking / Interceptable? | Description |
|---|---|---|
| \`session_start\` / \`session_end\` | No | Triggered when starting or closing a session |
| \`agent_start\` / \`agent_end\` | No | Triggered when the agent loop starts or completes |
| \`turn_start\` / \`turn_end\` | No | Fires before and after each reasoning turn |
| \`tool_call\` | **Yes** | Fires before a tool runs; can block or modify arguments |
| \`tool_result\` | **Yes** | Fires after a tool completes; can overwrite results |
| \`compaction_start\` / \`end\` | No | Fires when condensing session logs |

By subscribing to \`tool_call\` or \`tool_result\`, an extension can perform static analysis, audit commands for security compliance, redact secrets, or format output before returning it to the LLM.

This local-first extension system brings extreme modularity to your agentic workflows.`,
};

export default post;
