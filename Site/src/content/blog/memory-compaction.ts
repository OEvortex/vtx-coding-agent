import type { BlogPost } from "../../types";

const post: BlogPost = {
    id: "memory-compaction",
    title: "Context Compaction: Keeping Conversations Lean",
    excerpt:
        "How Vtx automatically manages token limits through LLM-driven context compaction, summarizing history to keep the model focused.",
    category: "Context Engine",
    date: "May 15, 2026",
    readTime: "4 Min Read",
    content: `When engaging in long coding sessions, token usage accumulates rapidly. Standard chat histories eventually overflow the context window, causing performance degradation or high latency. 

Vtx addresses this with a smart **Context Compaction** engine that compresses previous conversation history when the token threshold is crossed.

### The Overflow Check

Vtx monitors the session's token usage (input, output, and cache tokens) on every turn. If the usage exceeds a configurable threshold (by default, **80% of the context window**):
1. The full conversation history is compiled.
2. A special summarization request is sent to the LLM.
3. The resulting summary replaces the history up to that point.

### The Compaction Schema

To ensure that the next turn doesn't lose critical task history, the LLM summarizes the conversation into a structured layout:

- **Goal**: What the user is trying to accomplish.
- **Instructions**: Key guidelines, constraints, and custom specifications.
- **Discoveries**: Learnings about the codebase, bug causes, or configurations discovered.
- **Accomplished**: Complete tasks, work-in-progress, and pending steps.
- **Relevant files / directories**: A structured list of targeted code files.

By replacing the large message backlog with a concise, structured status report, Vtx frees up context space, reduces API costs, and keeps the model focused on implementation.`,
};

export default post;
