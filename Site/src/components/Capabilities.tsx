import { useRef, useCallback } from "react";
import { Reveal } from "./Reveal";
import { SectionLabel } from "./SectionLabel";
import {
  Wrench,
  Brain,
  PuzzlePiece,
  Lightning,
  GitBranch,
  ShieldCheck,
} from "@phosphor-icons/react";

const capabilities = [
  {
    n: "01",
    title: "Single-agent loop",
    icon: Lightning,
    body: "An iterative prompt-to-tool-to-prompt loop with structured arguments, real-time thought token streaming, and clean state boundaries.",
    span: "lg:col-span-7",
    accent: false,
  },
  {
    n: "02",
    title: "Nine default tools",
    icon: Wrench,
    body: "read (with image viewing), edit, write, bash, find, skill, web_search, and ask_user. Each Pydantic-typed and cancellable.",
    span: "lg:col-span-5",
    accent: false,
  },
  {
    n: "03",
    title: "Context compaction",
    icon: Brain,
    body: "LLM-driven session summarization triggers automatically when context usage crosses 80%, preserving goal, progress, and file lists.",
    span: "lg:col-span-5",
    accent: true,
  },
  {
    n: "04",
    title: "Skills & slash commands",
    icon: PuzzlePiece,
    body: "Auto-discovered SKILL.md instruction folders. Register skills as interactive slash commands (like /deploy) directly in the TUI.",
    span: "lg:col-span-7",
    accent: false,
  },
  {
    n: "05",
    title: "Python Extension API",
    icon: GitBranch,
    body: "Extend the agent by dropping a Python module into your workspace. Register new tools, slash commands, or hook into lifecycle events.",
    span: "lg:col-span-4",
    accent: false,
  },
  {
    n: "06",
    title: "Granular permissions",
    icon: ShieldCheck,
    body: "Work safely with prompt or auto approval modes. A customizable safe-command allowlist skips confirmations for read-only shell commands.",
    span: "lg:col-span-4",
    accent: false,
  },
  {
    n: "07",
    title: "TUI & Headless CLI",
    icon: Lightning,
    body: "Work interactively inside a custom terminal dashboard (Textual) or trigger headless single-prompt scripts via CLI pipe integrations.",
    span: "lg:col-span-4",
    accent: true,
  },
];

function SpotlightCard({ cap, index }: { cap: typeof capabilities[number]; index: number }) {
  const ref = useRef<HTMLDivElement>(null);

  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const el = ref.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    el.style.setProperty("--mouse-x", `${e.clientX - rect.left}px`);
    el.style.setProperty("--mouse-y", `${e.clientY - rect.top}px`);
  }, []);

  const Icon = cap.icon;

  return (
    <Reveal
      ref={ref}
      delay={0.04 * index}
      onMouseMove={handleMouseMove}
      className={`${cap.span} spotlight-card group relative bg-surface border border-hairline rounded-xl p-7 sm:p-8 flex flex-col gap-4 hover-lift`}
    >
      {cap.accent && (
        <div
          className="absolute inset-0 -z-0 opacity-50 pointer-events-none"
          aria-hidden="true"
          style={{
            background:
              "radial-gradient(60% 80% at 100% 0%, rgba(163, 230, 53, 0.06), transparent 60%)",
          }}
        />
      )}
      <div className="relative flex items-center justify-between">
        <div
          className={`grid place-items-center w-10 h-10 rounded-lg border ${
            cap.accent
              ? "border-accent/30 bg-accent/10 text-accent"
              : "border-hairline bg-canvas text-ink-muted"
          }`}
        >
          <Icon size={18} weight="duotone" />
        </div>
        <span className="font-mono text-[10.5px] tracking-[0.18em] text-ink-faint">
          {cap.n}
        </span>
      </div>
      <div className="relative">
        <h3 className="text-[20px] sm:text-[22px] font-semibold tracking-tight text-ink">
          {cap.title}
        </h3>
        <p className="mt-2.5 text-[14px] text-ink-muted leading-[1.65] max-w-[44ch]">
          {cap.body}
        </p>
      </div>
    </Reveal>
  );
}

export default function Capabilities() {
  return (
    <section
      id="capabilities"
      className="bg-canvas border-t border-hairline py-24 sm:py-32 px-5 sm:px-7"
    >
      <div className="max-w-[1400px] mx-auto">
        <Reveal>
          <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-6 pb-10 border-b border-hairline">
            <div>
              <SectionLabel index="02">Capabilities</SectionLabel>
              <h2 className="mt-4 text-display text-ink text-[36px] sm:text-[44px] lg:text-[56px] font-semibold max-w-[20ch]">
                Everything you need, nothing you don't.
              </h2>
            </div>
            <p className="text-[14px] text-ink-muted max-w-[42ch] leading-[1.6]">
              A small set of well-designed primitives beats a sprawling feature
              matrix. Vtx ships the loops, the state, the tools, and the
              extension hooks. The rest is yours to build.
            </p>
          </div>
        </Reveal>

        <div className="mt-8 grid grid-cols-1 lg:grid-cols-12 gap-3">
          {capabilities.map((cap, i) => (
            <SpotlightCard key={cap.n} cap={cap} index={i} />
          ))}
        </div>
      </div>
    </section>
  );
}
