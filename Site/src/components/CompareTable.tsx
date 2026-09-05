import { motion } from "motion/react";
import { Check, Minus } from "@phosphor-icons/react";

const ROWS: { k: string; vtx: string; cc: string; cur: string; oc: string; win?: boolean }[] = [
  { k: "Runtime tokens", vtx: "~2.6k auditable", cc: "10k+ hidden", cur: "hidden", oc: "~8k", win: true },
  { k: "Tools", vtx: "10 surgical", cc: "20+", cur: "IDE bundle", oc: "30+", win: true },
  { k: "Providers", vtx: "50+ BYO", cc: "Anthropic", cur: "Curated", oc: "75+ BYO", },
  { k: "Surfaces", vtx: "TUI · CLI · SDK", cc: "Terminal·IDE·Web", cur: "IDE · Cloud", oc: "Terminal·Web", },
  { k: "Local models", vtx: "Ollama · vLLM", cc: "—", cur: "—", oc: "Ollama", },
  { k: "License", vtx: "Apache-2.0", cc: "Proprietary", cur: "Proprietary", oc: "MIT", },
];

export default function CompareTable() {
  return (
    <section className="relative border-y border-white/10 bg-[#09090B] px-4 sm:px-8 py-20">
      <div className="mx-auto max-w-[1100px]">
        <div className="text-center">
          <div className="mono-caption-bright">WHY SWITCH — RECEIPTS</div>
          <h2 className="insane-h2 mt-3">VTX vs <span className="title-stroke-sm">THE BLOAT</span></h2>
        </div>
        <motion.div initial={{ opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }}
          className="mt-10 overflow-x-auto rounded-2xl border border-white/10">
          <table className="w-full min-w-[640px] text-left text-[13px]">
            <thead>
              <tr className="border-b border-white/10 bg-white/[0.02] font-mono text-[11px] tracking-widest text-zinc-500">
                <th className="px-5 py-4 font-medium"></th>
                <th className="px-5 py-4 font-bold text-lime-300">VTX</th>
                <th className="px-5 py-4 font-medium">CLAUDE CODE</th>
                <th className="px-5 py-4 font-medium">CURSOR</th>
                <th className="px-5 py-4 font-medium">OPENCODE</th>
              </tr>
            </thead>
            <tbody>
              {ROWS.map((r) => (
                <tr key={r.k} className="border-b border-white/5 last:border-0 hover:bg-white/[0.015]">
                  <td className="px-5 py-3.5 font-mono text-[12px] text-zinc-500">{r.k}</td>
                  <td className="px-5 py-3.5 font-semibold text-white">
                    <span className="inline-flex items-center gap-1.5">
                      {r.win ? <Check size={14} weight="bold" className="text-lime-300" /> : <span className="w-3.5" />}
                      {r.vtx}
                    </span>
                  </td>
                  <td className="px-5 py-3.5 text-zinc-500">{r.cc}</td>
                  <td className="px-5 py-3.5 text-zinc-500">{r.cur}</td>
                  <td className="px-5 py-3.5 text-zinc-500">{r.oc}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </motion.div>
        <p className="mt-4 flex items-center justify-center gap-1.5 text-center font-mono text-[11px] text-zinc-600">
          <Minus size={12} /> numbers from public docs + local token counts (o200k_base). audit it yourself.
        </p>
      </div>
    </section>
  );
}
