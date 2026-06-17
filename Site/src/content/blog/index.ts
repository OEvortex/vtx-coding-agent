import type { BlogPost } from "../../types";
import agenticLoop from "./agentic-loop";
import memoryCompaction from "./memory-compaction";
import mcpExtensions from "./mcp-extensions";
import agentskill from "./agentskill";

const blogPosts: BlogPost[] = [agentskill, agenticLoop, memoryCompaction, mcpExtensions];

export default blogPosts;
export { agentskill, agenticLoop, memoryCompaction, mcpExtensions };
export type { BlogPost };
