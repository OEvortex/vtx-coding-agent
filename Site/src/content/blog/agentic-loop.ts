import type { BlogPost } from "../../types";

const post: BlogPost = {
    id: "agentic-loop",
    title: "Single-Agent Loops vs Multi-Agent Frameworks",
    excerpt:
        "Why Vtx chose a single-agent architecture with tool calling over multi-agent orchestration, and how it achieves better reliability.",
    category: "Architecture",
    date: "May 22, 2026",
    readTime: "5 Min Read",
    content: `Most AI coding assistants either use a single REPL loop or a complex multi-agent framework. Vtx takes a middle path: a **single-agent agentic loop** with tool calling, concurrent execution, and clean state boundaries.

The Vtx runtime loop iterates: call LLM → execute tool calls → feed results back → repeat. This loop handles core tools, custom extensions, and skill-registered workflows through a unified runtime.

### Key Design Decisions

- **Lean Core Prompt**: Keeping the base system prompt under ~2,200 tokens (including guidelines and env status) to maximize available reasoning context window.
- **Background command execution**: The bash tool can spawn background tasks asynchronously, allowing the loop to query progress or run other tests.
- **Per-tool permission gating**: Each tool call passes through the permission check system, prompting on mutating actions or matching commands outside safe lists.
- **Structured Outputs**: Fully pydantic-validated arguments and response structures ensuring robustness.

This approach avoids the coordination overhead of multi-agent systems while maintaining the precision and flexibility needed for complex coding tasks.`,
};

export default post;
