export interface DocEntry {
  id: string;
  title: string;
  description: string;
  category: string;
  content: string;
  load: () => Promise<string>;
}

interface DocMetadata {
  id: string;
  title: string;
  description: string;
  category: string;
  fileName: string;
}

const docsMetadata: DocMetadata[] = [
  // Getting Started
  {
    id: "readme",
    title: "Documentation Index",
    description: "Overview of all available documentation and quick navigation",
    category: "Getting Started",
    fileName: "README.md",
  },
  {
    id: "configuration",
    title: "Configuration",
    description: "Every YAML config field with defaults, validation, and CLI overrides",
    category: "Getting Started",
    fileName: "configuration.md",
  },
  {
    id: "providers",
    title: "Providers & Models",
    description: "Built-in LLM providers, OAuth, API keys, dynamic catalogs, local models",
    category: "Getting Started",
    fileName: "providers.md",
  },

  // Tools
  {
    id: "tools",
    title: "Core Tools",
    description: "The 5 core tools: read, edit, write, bash, find. With parameters and examples",
    category: "Tools",
    fileName: "tools.md",
  },

  // Features
  {
    id: "skills",
    title: "Skills System",
    description: "Authoring skills: frontmatter, $ARGUMENTS, register_cmd, discovery paths",
    category: "Features",
    fileName: "skills.md",
  },
  {
    id: "extensions",
    title: "Extension System",
    description: "Plugin architecture, lifecycle, and the ExtensionAPI",
    category: "Features",
    fileName: "extensions.md",
  },
  {
    id: "permissions",
    title: "Permissions",
    description: "The prompt/auto modes, safe-command allowlist, and decision algorithm",
    category: "Features",
    fileName: "permissions.md",
  },
  {
    id: "sessions",
    title: "Sessions",
    description: "JSONL session format, resume, handoff, export, and compaction",
    category: "Features",
    fileName: "sessions.md",
  },
  {
    id: "theming",
    title: "Theming",
    description: "Built-in theme catalog and palette tokens",
    category: "Features",
    fileName: "theming.md",
  },
  {
    id: "headless",
    title: "Headless Mode",
    description: "Non-interactive prompt mode for scripts, CI, and automation",
    category: "Features",
    fileName: "headless.md",
  },
  {
    id: "local-models",
    title: "Local Models",
    description: "Running local models with llama-server and OpenAI-compatible endpoints",
    category: "Features",
    fileName: "local-models.md",
  },

  // Reference
  {
    id: "architecture",
    title: "Architecture",
    description: "Internal module map, message types, request flow, design decisions",
    category: "Reference",
    fileName: "architecture.md",
  },
  {
    id: "storage-layout",
    title: "Storage Layout",
    description: "Every file Vtx touches on disk: config, sessions, models, auth",
    category: "Reference",
    fileName: "storage-layout.md",
  },
  {
    id: "development",
    title: "Development",
    description: "Build, test, lint, typecheck, and release Vtx itself",
    category: "Reference",
    fileName: "development.md",
  },
  {
    id: "e2e-test-coverage-review",
    title: "E2E Test Coverage Review",
    description: "State of the tmux e2e harness and recommended additions",
    category: "Reference",
    fileName: "e2e-test-coverage-review.md",
  },

  // SDK Reference
  {
    id: "sdk-readme",
    title: "SDK Overview",
    description: "Programmatic, multi-agent interface built on Vtx's runtime",
    category: "SDK",
    fileName: "sdk/README.md",
  },
  {
    id: "sdk-agents",
    title: "SDK Agents",
    description: "LLM, instructions, tools, and handoffs configurations",
    category: "SDK",
    fileName: "sdk/agents.md",
  },
  {
    id: "sdk-approvals",
    title: "SDK Approvals",
    description: "Pause execution for human review mid-run",
    category: "SDK",
    fileName: "sdk/approvals.md",
  },
  {
    id: "sdk-guardrails",
    title: "SDK Guardrails",
    description: "Input, output, and tool-level safety checks",
    category: "SDK",
    fileName: "sdk/guardrails.md",
  },
  {
    id: "sdk-multi-agent",
    title: "SDK Multi-Agent",
    description: "Multi-agent delegation and handoff primitives",
    category: "SDK",
    fileName: "sdk/multi_agent.md",
  },
  {
    id: "sdk-permissions",
    title: "SDK Permissions",
    description: "Pluggable tool-call permission policies",
    category: "SDK",
    fileName: "sdk/permissions.md",
  },
  {
    id: "sdk-runner",
    title: "SDK Runner",
    description: "The execution entry point for running agents",
    category: "SDK",
    fileName: "sdk/runner.md",
  },
  {
    id: "sdk-sessions",
    title: "SDK Sessions",
    description: "Pluggable session memory backends",
    category: "SDK",
    fileName: "sdk/sessions.md",
  },
  {
    id: "sdk-skills",
    title: "SDK Skills",
    description: "Loading markdown-driven skills into your agent",
    category: "SDK",
    fileName: "sdk/skills.md",
  },
  {
    id: "sdk-tools",
    title: "SDK Custom Tools",
    description: "Defining custom Python tools using decorators",
    category: "SDK",
    fileName: "sdk/tools.md",
  },
  {
    id: "sdk-tracing",
    title: "SDK Tracing",
    description: "Observability, spans, and event trace processors",
    category: "SDK",
    fileName: "sdk/tracing.md",
  },
];

const markdownFiles = import.meta.glob<string>("../../../../docs/**/*.md", {
  query: "?raw",
  import: "default",
  eager: true,
});

const docs: DocEntry[] = docsMetadata.map((meta) => {
  const path = `../../../../docs/${meta.fileName}`;
  const content = markdownFiles[path] || `Content not found for ${meta.fileName}`;
  return {
    id: meta.id,
    title: meta.title,
    description: meta.description,
    category: meta.category,
    content,
    load: () => Promise.resolve(content),
  };
});

export default docs;

export const categories = ["Getting Started", "Tools", "Features", "SDK", "Reference"];

export function getDocById(id: string): DocEntry | undefined {
  return docs.find((d) => d.id === id);
}

export function getDocsByCategory(category: string): DocEntry[] {
  return docs.filter((d) => d.category === category);
}
