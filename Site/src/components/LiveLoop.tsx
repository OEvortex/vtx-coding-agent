import { useEffect, useState } from "react";
import { motion, AnimatePresence, useReducedMotion } from "motion/react";

type Demo = { id: string; task: string; meta: string; lines: { k: string; t: string }[] };

const DEMOS: Demo[] = [
  {
    id: "refactor", task: "Refactor provider registry to load lazily", meta: "read → edit → bash · 1.8s",
    lines: [
      { k: "sys", t: "vtx v1.1.1 · 10 tools · prompt mode · 2,612 tokens loaded" },
      { k: "tool", t: "read   src/ai/providers/registry.py   142 lines" },
      { k: "tool", t: "edit   registry.py   +24 −31 · lazy import" },
      { k: "tool", t: "bash   uv run pytest tests/test_providers -q" },
      { k: "ok", t: "14 passed in 1.82s" },
      { k: "say", t: "Providers now resolve on first use. −3 eager imports." },
    ],
  },
  {
    id: "delegate", task: "Delegate sub-agent to write tests", meta: "task → goal · streams back",
    lines: [
      { k: "sys", t: "spawning isolated sub-agent · session tree fork" },
      { k: "tool", t: "task   tests for tools/task.py   streaming…" },
      { k: "tool", t: "goal   track: plan 4/4 · audit independent" },
      { k: "ok", t: "sub-agent done · 9 tests · merged to parent" },
      { k: "say", t: "Parent context untouched. Only the diff came back." },
    ],
  },
  {
    id: "switch", task: "Shift+Tab → security-audit agent", meta: "handoff · policy swap",
    lines: [
      { k: "sys", t: "handoff: default → security-audit · tools deny: bash:rm" },
      { k: "tool", t: "read   src/auth/tokens.py   88 lines" },
      { k: "tool", t: "skill  security-checklist   6 checks" },
      { k: "ok", t: "2 findings · 1 patch · audit logged" },
      { k: "say", t: "Same loop, stricter hands. Policy swapped live." },
    ],
  },
];

function Line({ k, t }: { k: string; t: string }) {
  if (k === "sys") return <div className="text-zinc-600">{t}</div>;
  if (k === "tool") return <div className="text-zinc-500">▸ {t}</div>;
  if (k === "ok") return <div className="text-lime-300">{t}</div>;
  return <div className="text-zinc-200">{t}</div>;
}

export default function LiveLoop() {
  const reduce = useReducedMotion();
  const [active, setActive] = useState(DEMOS[0]);
  const [n, setN] = useState(0);

  useEffect(() => {
    setN(0);
    if (reduce) {
      setN(active.lines.length);
      return;
    }
    const iv = setInterval(() => {
      setN((v) => (v >= active.lines.length ? v : v + 1));
    }, 620);
    return () => clearInterval(iv);
  }, [active, reduce]);

  return (
    <section className="relative bg-[#070708] px-4 sm:px-8 py-20 overflow-hidden">
      <div className="mx-auto max-w-[1240px]">
        <div className="mono-caption-bright">LIVE LOOP — CLICK A TASK, WATCH IT RUN</div>
        <h2 className="insane-h2 mt-3">NOT A VIDEO.<br /><span className="text-lime-300">A REHEARSAL.</span></h2>

        <div className="mt-8 grid gap-3 lg:grid-cols-12">
          <div className="lg:col-span-4 flex flex-col gap-2">
            {DEMOS.map((d, i) => (
              <button
                key={d.id}
                onClick={() => setActive(d)}
                className={`rounded-xl border p-4 text-left transition-all ${
                  active.id === d.id
                    ? "border-lime-400/50 bg-lime-400/[0.06]"
                    : "border-white/10 bg-white/[0.02] hover:border-white/25"
                }`}
              >
                <div className="font-mono text-[10px] tracking-[0.2em] text-zinc-600">0{i + 1}</div>
                <div className={`mt-1 text-[14px] font-semibold ${active.id === d.id ? "text-white" : "text-zinc-300"}`}>{d.task}</div>
                <div className="mt-1 font-mono text-[11px] text-zinc-600">{d.meta}</div>
              </button>
            ))}
            <div className="rounded-xl border border-white/10 p-4 font-mono text-[11.5px] leading-relaxed text-zinc-500">
              <span className="text-lime-300">$</span> vtx -p "do it headless"<br />
              same loop in CI. no TUI needed.
            </div>
          </div>

          <div className="lg:col-span-8 overflow-hidden rounded-2xl border border-white/10 bg-[#0B0B0D]">
            <div className="flex items-center gap-2 border-b border-white/10 px-4 h-11">
              <span className="w-2.5 h-2.5 rounded-full bg-red-500/70" />
              <span className="w-2.5 h-2.5 rounded-full bg-amber-500/70" />
              <span className="w-2.5 h-2.5 rounded-full bg-green-500/70" />
              <span className="ml-2 font-mono text-[11px] text-zinc-500">vtx — {active.id}</span>
              <span className="ml-auto flex items-center gap-1.5 font-mono text-[10px] text-lime-300">
                <span className="dot-pulse" /> running
              </span>
            </div>
            <div className="min-h-[280px] p-5 font-mono text-[12.5px] leading-[1.9]">
              <div className="mb-3 flex gap-2">
                <span className="text-lime-300">❯</span>
                <span className="text-zinc-100">{active.task}</span>
              </div>
              <AnimatePresence mode="popLayout">
                {active.lines.slice(0, n).map((l, i) => (
                  <motion.div key={`${active.id}-${i}`} initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} className="pl-6">
                    <Line k={l.k} t={l.t} />
                  </motion.div>
                ))}
              </AnimatePresence>
              {n < active.lines.length && <span className="term-caret ml-6 mt-1" />}
            </div>
            <div className="flex items-center justify-between border-t border-white/10 px-4 h-9 font-mono text-[10px] text-zinc-600">
              <span>TUI · CLI · SDK — one loop</span>
              <span>utf-8 · bash · prompt mode</span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
