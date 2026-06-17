import { useState, useEffect, useRef } from "react";
import { motion, useReducedMotion } from "motion/react";
import {
  ArrowLeft,
  Robot,
  Wrench,
  Brain,
  PuzzlePiece,
  BookOpen,
  Monitor,
  Shield,
  GitBranch,
  Cpu,
  Palette,
  Pulse,
  Eye,
  Check,
  Network,
  Lightbulb,
  Plug,
} from "@phosphor-icons/react";
import { Reveal } from "./Reveal";
import { SectionLabel } from "./SectionLabel";

interface FeatureItem {
  label: string;
  description: string;
}

interface FeatureSection {
  id: string;
  title: string;
  subtitle: string;
  icon: typeof Robot;
  features: FeatureItem[];
  specs?: { label: string; value: string }[];
}

const FEATURE_SECTIONS: FeatureSection[] = [
  {
    id: "interfaces",
    title: "TUI & Headless CLI",
    subtitle: "Two distinct run modes to suit interactive terminal work or script-driven automation.",
    icon: Monitor,
    features: [
      { label: "Terminal UI (TUI)", description: "Textual-powered keyboard-driven terminal dashboard. Live thought token streaming, interactive tool approval prompts, model selector widget, session browser, and real-time statistics." },
      { label: "Headless CLI", description: "Non-interactive one-off prompt runner via the -p / --prompt flag. Ideal for shell integration, piped commands, scripting, and CI/CD pipelines." },
      { label: "Direct Shell Execution", description: "Run shell commands directly from the TUI input using !command. Use !!command to feed the stdout of a command directly back to the LLM for immediate analysis." },
      { label: "Collapsible Thinking", description: "The TUI automatically collapses deep thinking chains once a turn finishes, keeping your screen readable." },
    ],
    specs: [
      { label: "UI Themes", value: "25" },
      { label: "TUI Tool", value: "Textual" },
      { label: "CLI Modes", value: "2" },
    ],
  },
  {
    id: "tools",
    title: "Core Toolset",
    subtitle: "9 built-in tools tailored for codebase research, editing, command execution, and verification.",
    icon: Wrench,
    features: [
      { label: "File Operations", description: "read (handles large files with offset/limit paging and images), write (create or completely rewrite files), edit (apply precise search-and-replace blocks)." },
      { label: "System & Discovery", description: "bash (runs shell processes with timeouts and background task spawning), find (discovers workspace files respecting project .gitignore rules)." },
      { label: "Web Capabilities", description: "fetch_webpage (converts raw page HTML into clean markdown), web_search (queries Exa or Brave APIs for real-time web references)." },
      { label: "User Interaction", description: "ask_user (allows the agent to pose multiple-choice options or custom questions to the user during long executions)." },
      { label: "Skills Tool", description: "skill (lets the agent list, view, create, patch, edit, or delete skill instruction folders dynamically)." },
    ],
    specs: [
      { label: "Default Tools", value: "9" },
      { label: "Paging Limit", value: "800 lines" },
      { label: "File Types", value: "Text & Image" },
    ],
  },
  {
    id: "skills",
    title: "Skills System",
    subtitle: "Extend agent capabilities via workspace or global instruction files with auto-discovered slash commands.",
    icon: BookOpen,
    features: [
      { label: "Workspace & Global Scopes", description: "Scan for custom instructions from project-local .agents/skills/ or user-global ~/.agents/skills/. Workspace skills take precedence." },
      { label: "Command Registration", description: "Setting register_cmd: true in a skill's YAML frontmatter automatically registers it as a custom slash command (e.g. /my-skill) in the TUI." },
      { label: "Progressive Context Loading", description: "Skills are loaded into the system prompt context on demand only when activated or invoked, keeping token footprint minimal." },
      { label: "Local-First Authoring", description: "Author skills simply by writing a SKILL.md containing a description, command info, and markdown instructions." },
    ],
    specs: [
      { label: "Default Scopes", value: "3" },
      { label: "Format", value: "YAML/Markdown" },
      { label: "Integration", value: "Slash Cmds" },
    ],
  },
  {
    id: "extensions",
    title: "Python Extension API",
    subtitle: "Customize Vtx by registering custom tools, slash commands, or listening to agent lifecycle events.",
    icon: PuzzlePiece,
    features: [
      { label: "Self-Extensibility", description: "Drop a Python file in ~/.vtx/agent/extensions/, .vtx/extensions/, or load them at runtime via the --extension flag." },
      { label: "Custom Tool Registration", description: "Register Pydantic-validated classes inheriting from BaseTool to add new API capabilities for the LLM to call." },
      { label: "Slash Command Registration", description: "Contribute custom slash commands with custom logic that runs directly inside the interactive agent context." },
      { label: "Event Listeners", description: "Subscribe to lifecycle stages like session start/end, agent start/end, turn start/end, tool calls, and context compactions." },
      { label: "Blocking Interceptors", description: "Hook into tool_call or tool_result to inspect arguments, block dangerous actions, or modify outputs before the LLM processes them." },
    ],
    specs: [
      { label: "Language", value: "Python" },
      { label: "Discovery paths", value: "4" },
      { label: "Lifecycle Hooks", value: "10" },
    ],
  },
  {
    id: "llm",
    title: "LLM & Model Gateway",
    subtitle: "Connect unauthenticated local models or hosted APIs with configurable thinking levels.",
    icon: Cpu,
    features: [
      { label: "Hosted Providers", description: "Native support for OpenAI, Anthropic, DeepSeek, Azure AI Foundry, GitHub Copilot, and ZhiPu." },
      { label: "Local Models", description: "Integrates with unauthenticated local endpoints like llama-server and Ollama via OpenAI-compatible base URLs." },
      { label: "Fuzzy Model Matching", description: "Resolves short names or partial matches to the closest available provider/model string dynamically." },
      { label: "Thinking Level Selection", description: "Select the level of reasoning tokens to produce (none, minimal, low, medium, high, xhigh) based on model capabilities." },
    ],
    specs: [
      { label: "Built-in Providers", value: "11+" },
      { label: "Local Support", value: "Yes" },
      { label: "Authentication", value: "Keys & OAuth" },
    ],
  },
  {
    id: "sessions",
    title: "Sessions & Compaction",
    subtitle: "Robust context management with append-only session logs, handoffs, and auto-compaction.",
    icon: Brain,
    features: [
      { label: "Append-only JSONL Logs", description: "Conversations are saved in structured, human-readable JSONL formats, making them easy to diff, grep, or backup." },
      { label: "Session Resume & Handoff", description: "Resume any session (vtx -c or vtx -r ID). Trigger /handoff inside the TUI to summarize and start a fresh session with that context." },
      { label: "HTML Transcript Export", description: "Export the entire interactive session into a beautiful, styled, self-contained HTML file using the /export command." },
      { label: "Auto-Compaction", description: "When context usage crosses 80%, the LLM automatically summarizes the session state (Goal, Instructions, Accomplished, Files) to reclaim token space." },
    ],
    specs: [
      { label: "Log Format", value: "JSONL" },
      { label: "Compaction Gate", value: "80%" },
      { label: "Resumable", value: "Yes" },
    ],
  },
  {
    id: "permissions",
    title: "Granular Permissions",
    subtitle: "Stay in full control of execution safety with configurable approval modes.",
    icon: Shield,
    features: [
      { label: "Prompt Approval Mode", description: "Default mode. Prompts you for confirmation before executing mutating actions like file writes, edits, or running bash commands." },
      { label: "Auto Mode", description: "Unrestricted execution. Speeds up workflows when running in trusted sandboxes or doing safe iterations." },
      { label: "Safe-Command Allowlist", description: "Commands like cat, head, ls, git status, git diff, and logs skip permission prompts in TUI mode to minimize friction." },
      { label: "Safety Profiles", description: "Define custom allowed paths, command patterns, or restrict actions to the project workspace directory." },
    ],
    specs: [
      { label: "Modes", value: "Prompt / Auto" },
      { label: "Command Bypass", value: "Allowlist" },
      { label: "Mutating Check", value: "Yes" },
    ],
  },
];

/* ------------------------------------------------------------------ */
/*  Side nav                                                           */
/* ------------------------------------------------------------------ */

function SideNav({ activeId }: { activeId: string }) {
  return (
    <nav className="hidden lg:flex flex-col gap-0.5 sticky top-28 self-start w-56">
      {FEATURE_SECTIONS.map((s) => {
        const Icon = s.icon;
        const isActive = activeId === s.id;
        return (
          <a
            key={s.id}
            href={`#${s.id}`}
            onClick={(e) => {
              e.preventDefault();
              document
                .getElementById(s.id)
                ?.scrollIntoView({ behavior: "smooth", block: "start" });
            }}
            className={`flex items-center gap-2.5 px-3 py-2 text-[12.5px] rounded-md transition-colors border-l-2 ${
              isActive
                ? "border-accent text-ink bg-surface"
                : "border-transparent text-ink-muted hover:text-ink hover:bg-surface"
            }`}
          >
            <Icon
              size={14}
              weight="duotone"
              className={isActive ? "text-accent" : "text-ink-faint"}
            />
            {s.title}
          </a>
        );
      })}
    </nav>
  );
}

/* ------------------------------------------------------------------ */
/*  Feature card                                                       */
/* ------------------------------------------------------------------ */

function FeatureCard({ feature, index }: { feature: FeatureItem; index: number }) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      className="group flex gap-4 p-4 bg-surface border border-hairline rounded-lg hover:border-hairline-strong hover:bg-surface-2 transition-colors"
      initial={reduce ? false : { opacity: 0, y: 12 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.2 }}
      transition={{ duration: 0.4, delay: index * 0.04, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="shrink-0 mt-0.5">
        <div className="w-5 h-5 rounded border border-hairline-strong flex items-center justify-center bg-canvas">
          <Check size={11} weight="bold" className="text-accent" />
        </div>
      </div>
      <div className="text-left space-y-1.5">
        <p className="text-[13px] font-medium text-ink">{feature.label}</p>
        <p className="text-[12.5px] text-ink-muted leading-[1.6]">
          {feature.description}
        </p>
      </div>
    </motion.div>
  );
}

/* ------------------------------------------------------------------ */
/*  Spec badge                                                         */
/* ------------------------------------------------------------------ */

function SpecBadge({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col items-center p-3 bg-surface-2 border border-hairline rounded-lg">
      <span className="numeric text-ink text-[18px] sm:text-[20px] font-semibold">
        {value}
      </span>
      <span className="font-mono text-[9.5px] tracking-[0.18em] text-ink-faint mt-1.5 uppercase">
        {label}
      </span>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Section                                                            */
/* ------------------------------------------------------------------ */

function FeatureSection({
  section,
  index,
}: {
  section: FeatureSection;
  index: number;
}) {
  const reduce = useReducedMotion();
  const Icon = section.icon;
  const reversed = index % 2 === 1;

  return (
    <section id={section.id} className="scroll-mt-28">
      <motion.div
        className="border border-hairline rounded-xl bg-surface p-6 sm:p-8 lg:p-10 space-y-7"
        initial={reduce ? false : { opacity: 0, y: 20 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, amount: 0.1 }}
        transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
      >
        <div
          className={`flex flex-col ${
            reversed ? "sm:flex-row-reverse" : "sm:flex-row"
          } sm:items-start justify-between gap-5`}
        >
          <div className="space-y-3 text-left flex-1">
            <div className="flex items-center gap-3">
              <div className="grid place-items-center w-9 h-9 rounded-md border border-hairline-strong bg-canvas text-accent">
                <Icon size={16} weight="duotone" />
              </div>
              <span className="font-mono text-[10.5px] tracking-[0.18em] text-ink-faint uppercase">
                Section {String(index + 1).padStart(2, "0")}
              </span>
            </div>
            <h3 className="text-[24px] sm:text-[30px] font-semibold tracking-tight text-ink leading-[1.15]">
              {section.title}
            </h3>
            <p className="text-[14px] leading-[1.6] max-w-[60ch] text-ink-muted">
              {section.subtitle}
            </p>
          </div>
        </div>

        {section.specs && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
            {section.specs.map((spec) => (
              <SpecBadge key={spec.label} label={spec.label} value={spec.value} />
            ))}
          </div>
        )}

        <div className="h-px bg-hairline" />

        <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
          {section.features.map((feature, i) => (
            <FeatureCard key={feature.label} feature={feature} index={i} />
          ))}
        </div>
      </motion.div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Page                                                               */
/* ------------------------------------------------------------------ */

export default function FeaturesPage() {
  const [activeSection, setActiveSection] = useState(FEATURE_SECTIONS[0].id);
  const reduce = useReducedMotion();
  const ticking = useRef(false);

  useEffect(() => {
    const handleScroll = () => {
      if (ticking.current) return;
      ticking.current = true;
      requestAnimationFrame(() => {
        const ids = FEATURE_SECTIONS.map((s) => s.id);
        const mid = window.innerHeight / 2;
        let closest = ids[0];
        let closestDist = Infinity;
        for (const id of ids) {
          const el = document.getElementById(id);
          if (!el) continue;
          const rect = el.getBoundingClientRect();
          const elMid = rect.top + rect.height / 2;
          const dist = Math.abs(elMid - mid);
          if (dist < closestDist) {
            closestDist = dist;
            closest = id;
          }
        }
        setActiveSection(closest);
        ticking.current = false;
      });
    };

    handleScroll();
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  useEffect(() => {
    const hash = window.location.hash.replace("#", "");
    if (hash) {
      setTimeout(() => {
        document
          .getElementById(hash)
          ?.scrollIntoView({ behavior: "smooth", block: "start" });
      }, 100);
    }
  }, []);

  return (
    <div className="bg-canvas min-h-screen text-ink font-sans antialiased">
      {/* Header bar */}
      <header className="fixed top-0 left-0 right-0 z-50 bg-canvas/85 backdrop-blur-md border-b border-hairline">
        <div className="max-w-[1400px] mx-auto px-5 sm:px-7 flex items-center justify-between h-14">
          <a
            href="/"
            className="flex items-center gap-1.5 text-[12px] text-ink-muted hover:text-ink transition-colors"
          >
            <ArrowLeft size={13} weight="bold" />
            Back to home
          </a>
          <a href="/" className="flex items-center gap-2">
            <span className="grid place-items-center w-6 h-6 rounded-md bg-accent text-[#0A0A0B]">
              <svg
                width="12"
                height="12"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <polyline points="4 17 10 11 4 5" />
                <line x1="12" y1="19" x2="20" y2="19" />
              </svg>
            </span>
            <span className="text-[14px] font-semibold tracking-tight text-ink">
              Vtx
            </span>
            <span className="font-mono text-[10.5px] text-ink-faint tracking-tight">
              v0.4
            </span>
          </a>
          <a
            href="/docs/"
            className="text-[12px] text-ink-muted hover:text-ink transition-colors"
          >
            Docs
          </a>
        </div>
      </header>

      {/* Hero */}
      <section className="pt-32 pb-16 sm:pb-20 px-5 sm:px-7">
        <div className="max-w-[1400px] mx-auto text-left space-y-6">
          <Reveal>
            <SectionLabel>Complete capability reference</SectionLabel>
          </Reveal>
          <Reveal delay={0.05}>
            <h1 className="text-display text-ink text-[40px] sm:text-[52px] lg:text-[72px] font-semibold max-w-[18ch]">
              Every feature,
              <br />
              <span className="text-ink-muted">fully documented.</span>
            </h1>
          </Reveal>
          <Reveal delay={0.1}>
            <p className="text-[15px] text-ink-muted max-w-[60ch] leading-[1.65]">
              Vtx v0.4 is a next-generation agentic harness. Roughly 225K lines
              of code across 902 source files with a single-agent loop
              orchestrating 120 plus tools, 116 skills, 6 extensions, 6
              watchers, a full MCP client, and native Claude Code plus OpenAI
              Codex plugin support. Here is everything it can do.
            </p>
          </Reveal>

          <Reveal delay={0.15}>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 max-w-3xl pt-6">
              {[
                { value: "300+", label: "Features" },
                { value: "120+", label: "Tools" },
                { value: "116", label: "Skills" },
                { value: "6", label: "Extensions" },
              ].map((stat) => (
                <div
                  key={stat.label}
                  className="p-4 border border-hairline bg-surface rounded-lg"
                >
                  <span className="numeric text-ink text-[26px] sm:text-[32px] font-semibold block">
                    {stat.value}
                  </span>
                  <span className="font-mono text-[9.5px] tracking-[0.18em] text-ink-faint uppercase block mt-1.5">
                    {stat.label}
                  </span>
                </div>
              ))}
            </div>
          </Reveal>
        </div>
      </section>

      <div className="max-w-[1400px] mx-auto px-5 sm:px-7">
        <div className="h-px bg-hairline" />
      </div>

      {/* Main content */}
      <div className="max-w-[1400px] mx-auto px-5 sm:px-7 py-14 sm:py-20">
        <div className="flex gap-12 lg:gap-16">
          <SideNav activeId={activeSection} />

          <div className="flex-1 space-y-6 lg:space-y-8 min-w-0">
            {FEATURE_SECTIONS.map((section, index) => (
              <FeatureSection
                key={section.id}
                section={section}
                index={index}
              />
            ))}
          </div>
        </div>
      </div>

      <footer className="border-t border-hairline py-10 px-5 sm:px-7">
        <div className="max-w-[1400px] mx-auto flex flex-col sm:flex-row items-center justify-between gap-4 text-[11.5px] font-mono text-ink-faint">
          <div className="flex items-center gap-3">
            <span className="font-semibold tracking-tight text-ink">Vtx</span>
            <span className="text-ink-faint">v0.4.2</span>
          </div>
          <div className="flex items-center gap-6">
            <a href="/" className="hover:text-ink transition-colors">Home</a>
            <a href="/docs/" className="hover:text-ink transition-colors">Docs</a>
            <a
              href="https://github.com/OEvortex/vtx-coding-agent"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-ink transition-colors"
            >
              GitHub
            </a>
          </div>
          <p>© 2026 OEvortex</p>
        </div>
      </footer>
    </div>
  );
}
