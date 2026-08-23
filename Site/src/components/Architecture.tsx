import { Reveal } from "./Reveal";
import { SectionLabel } from "./SectionLabel";

/**
 * The v1.0.0 mono-repo: four top-level packages under src/.
 * Rendered as a file-tree card + package description cards.
 */

const packages = [
  {
    path: "src/ai",
    title: "ai",
    body: "LLM providers, model catalog, dynamic fetching, OAuth, SDK bindings.",
  },
  {
    path: "src/tui",
    title: "tui",
    body: "Textual UI — commands, widgets, selection mode, completion.",
  },
  {
    path: "src/coding_agent",
    title: "coding_agent",
    body: "Headless CLI, config, built-in skills.",
  },
  {
    path: "src/core",
    title: "core",
    body: "Paths, tracing, permissions, events, compaction, scratchpad.",
  },
];

const treeLines = [
  { d: 0, seg: [{ t: "src/", c: "text-zinc-200" }] },
  { d: 1, seg: [{ t: "├── ai", c: "text-accent" }, { t: "            # providers · catalog · oauth", c: "text-zinc-600" }] },
  { d: 1, seg: [{ t: "├── tui", c: "text-zinc-300" }, { t: "           # textual app · widgets", c: "text-zinc-600" }] },
  { d: 1, seg: [{ t: "├── coding_agent", c: "text-zinc-300" }, { t: "   # cli · config · skills", c: "text-zinc-600" }] },
  { d: 1, seg: [{ t: "└── core", c: "text-zinc-300" }, { t: "            # permissions · compaction", c: "text-zinc-600" }] },
];

export default function Architecture() {
  return (
    <section
      id="architecture"
      className="bg-canvas border-t border-hairline py-24 sm:py-32 px-5 sm:px-7"
    >
      <div className="max-w-[1400px] mx-auto">
        <Reveal>
          <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-6 pb-10 border-b border-hairline">
            <div>
              <SectionLabel index="03">Architecture</SectionLabel>
              <h2 className="mt-4 text-display text-ink text-[36px] sm:text-[44px] lg:text-[56px] font-semibold max-w-[22ch]">
                Four packages.
                <br />
                <span className="text-ink-muted">Zero mystery.</span>
              </h2>
            </div>
            <p className="text-[14px] text-ink-muted max-w-[42ch] leading-[1.6]">
              v1.0 splits the harness into a clean mono-repo. Every layer is
              importable, testable, and replaceable — the whole runtime fits
              in your head.
            </p>
          </div>
        </Reveal>

        <div className="mt-10 grid grid-cols-1 lg:grid-cols-12 gap-6 lg:gap-10">
          {/* File tree */}
          <Reveal delay={0.05} className="lg:col-span-5">
            <div className="relative bg-[#0C0C0E] border border-hairline rounded-xl overflow-hidden h-full min-h-[260px]">
              <div className="flex items-center gap-2 px-4 h-9 border-b border-hairline bg-surface/60">
                <span className="font-mono text-[11px] text-ink-faint">vtx-coding-agent</span>
              </div>
              <pre className="font-mono text-[12.5px] leading-[2.2] px-5 py-5 overflow-x-auto">
                {treeLines.map((l, i) => (
                  <div key={i} style={{ paddingLeft: `${l.d * 16}px` }}>
                    {l.seg.map((s, j) => (
                      <span key={j} className={s.c}>
                        {s.t}
                      </span>
                    ))}
                  </div>
                ))}
              </pre>
              <div className="absolute bottom-4 right-5 hidden sm:block">
                <span className="chip">4 packages</span>
              </div>
            </div>
          </Reveal>

          {/* Package cards */}
          <div className="lg:col-span-7 grid grid-cols-1 sm:grid-cols-2 gap-3">
            {packages.map((p, i) => (
              <Reveal
                key={p.path}
                delay={0.08 + i * 0.04}
                className="bg-surface border border-hairline rounded-xl p-5 flex flex-col gap-2 hover-lift"
              >
                <code className="font-mono text-[12px] text-accent">{p.path}</code>
                <p className="text-[13.5px] text-ink-muted leading-[1.6]">{p.body}</p>
              </Reveal>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
