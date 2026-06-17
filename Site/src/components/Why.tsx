import { Reveal } from "./Reveal";
import { SectionLabel } from "./SectionLabel";
import { ArrowUpRight } from "@phosphor-icons/react";

const principles = [
  {
    n: "01",
    title: "Lean by default",
    body: "Vtx runs on roughly 2,200 tokens of base prompt. Most agentic frameworks load thousands of hidden tokens per turn. We do not.",
  },
  {
    n: "02",
    title: "Transparent state",
    body: "Sessions are append-only JSONL on disk. You can grep them, diff them, hand them off, or export them as standalone HTML.",
  },
  {
    n: "03",
    title: "Pluggable everything",
    body: "Tools, hooks, slash commands, agents, and skills are all registered through one public API. The framework gets out of the way.",
  },
  {
    n: "04",
    title: "Real permissioning",
    body: "Per-tool decisions with a safe-command allowlist. Prompt mode asks, auto mode respects your rules. Nothing runs by surprise.",
  },
];

export default function Why() {
  return (
    <section
      id="why"
      className="bg-canvas border-t border-hairline py-24 sm:py-32 px-5 sm:px-7"
    >
      <div className="max-w-[1400px] mx-auto">
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-10 lg:gap-16">
          {/* Section header (left, vertical). */}
          <div className="lg:col-span-5">
            <Reveal>
              <SectionLabel index="01">Why Vtx</SectionLabel>
            </Reveal>
            <Reveal delay={0.05}>
              <h2 className="mt-5 text-display text-ink text-[36px] sm:text-[48px] lg:text-[56px] font-semibold">
                Built for developers
                <br />
                <span className="text-ink-muted">who read the source.</span>
              </h2>
            </Reveal>
            <Reveal delay={0.1}>
              <p className="mt-6 text-[15px] text-ink-muted max-w-[44ch] leading-[1.65]">
                Most AI coding tools hide what they are doing behind a chat
                box. Vtx shows you. The loop is plain Python, the prompt is
                inspectable, and every tool call is auditable. Open the
                repository and the harness is on the first page.
              </p>
            </Reveal>
            <Reveal delay={0.15}>
              <a
                href="https://github.com/OEvortex/vtx-coding-agent"
                target="_blank"
                rel="noopener noreferrer"
                className="mt-7 inline-flex items-center gap-1.5 text-[13px] text-accent font-medium hover:underline underline-offset-4"
              >
                <span>Read the source on GitHub</span>
                <ArrowUpRight size={13} weight="bold" />
              </a>
            </Reveal>
          </div>

          {/* Principles (right, 2x2). */}
          <div className="lg:col-span-7">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {principles.map((p, i) => (
                <Reveal
                  key={p.n}
                  delay={0.05 + i * 0.05}
                  className="bg-surface border border-hairline rounded-xl p-6 sm:p-7 flex flex-col gap-3 hover-lift"
                >
                  <span className="font-mono text-[10.5px] tracking-[0.18em] text-ink-faint">
                    {p.n}
                  </span>
                  <h3 className="text-[18px] font-semibold tracking-tight text-ink">
                    {p.title}
                  </h3>
                  <p className="text-[14px] text-ink-muted leading-[1.65]">
                    {p.body}
                  </p>
                </Reveal>
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
