import { useState } from "react";
import { motion } from "motion/react";

export default function TokenWar() {
  const [vtx, setVtx] = useState(2600);
  const other = 14000;
  const pct = Math.round((vtx / other) * 100);
  const saved = 100 - pct;

  return (
    <section className="relative overflow-hidden border-y border-white/10 bg-[#0B0D05] px-4 sm:px-8 py-20">
      <div className="war-grid" aria-hidden="true" />
      <div className="relative mx-auto max-w-[1440px] grid gap-10 lg:grid-cols-2 items-center">
        <div>
          <div className="mono-caption-bright">02 — TOKEN WAR</div>
          <h2 className="insane-h2 mt-3">DRAG IT.<br />FEEL THE <span className="text-red-400">BLOAT.</span></h2>
          <p className="mt-4 max-w-[44ch] text-[14.5px] leading-relaxed text-zinc-400">
            Slide Vtx runtime size. Watch how much context you hand back to <em>your code</em>.
            Fully auditable prompt — read it, shrink it, fork it.
          </p>
          <input
            type="range" min={1800} max={14000} step={100} value={vtx}
            onChange={(e) => setVtx(Number(e.target.value))}
            className="war-slider mt-8"
            aria-label="vtx token size"
          />
          <div className="mt-3 flex justify-between font-mono text-[11px] text-zinc-500">
            <span>1.8k — starved</span><span>14k — bloated™</span>
          </div>
        </div>
        <div className="war-panel">
          <div className="flex items-baseline justify-between">
            <span className="font-mono text-[11px] tracking-[0.2em] text-zinc-500">CONTEXT BATTLE</span>
            <motion.span key={saved} initial={{ scale: 1.4, color: "#bef264" }} animate={{ scale: 1, color: "#a3e635" }} className="numeric text-5xl font-bold">
              −{saved}%
            </motion.span>
          </div>
          <div className="mt-6 space-y-4">
            <div>
              <div className="mb-1 flex justify-between font-mono text-[11px]"><span className="text-lime-300">VTX · {vtx.toLocaleString()} tok</span><span className="text-zinc-500">{pct}%</span></div>
              <div className="h-4 overflow-hidden rounded-full bg-white/5 border border-white/10">
                <motion.div className="h-full rounded-full bg-gradient-to-r from-lime-500 to-lime-300" animate={{ width: `${pct}%` }} />
              </div>
            </div>
            <div>
              <div className="mb-1 flex justify-between font-mono text-[11px]"><span className="text-red-400">BLOATED AGENT · {other.toLocaleString()} tok</span><span className="text-zinc-500">100%</span></div>
              <div className="h-4 overflow-hidden rounded-full bg-white/5 border border-white/10">
                <div className="h-full w-full rounded-full bg-gradient-to-r from-red-900 to-red-500" />
              </div>
            </div>
          </div>
          <div className="mt-6 rounded-lg border border-lime-400/20 bg-lime-400/5 p-3 font-mono text-[12px] text-lime-200">
            ≈ {(saved * 42).toLocaleString()} more lines of YOUR code fit per turn.
          </div>
        </div>
      </div>
    </section>
  );
}
