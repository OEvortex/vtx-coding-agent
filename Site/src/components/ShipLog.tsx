import { motion } from "motion/react";

const LOG = [
  { v: "v1.1.1", d: "OpenJarvis gateway merge · cron + sessions · matrix/discord bridges", t: "latest" },
  { v: "v1.1.0", d: "Goals system: file-backed objectives, task tree, completion audit", t: "recent" },
  { v: "v1.0.0", d: "Mono-repo release · SDK + TUI + headless · 50+ providers", t: "stable" },
];

export default function ShipLog() {
  return (
    <section className="bg-[#070708] px-4 sm:px-8 py-20">
      <div className="mx-auto max-w-[1100px]">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="mono-caption-bright">SHIP LOG — WE MOVE FAST</div>
            <h2 className="insane-h2 mt-3">FRESH<br />OUT THE oven<span className="text-lime-300">.</span></h2>
          </div>
          <a href="https://github.com/OEvortex/vtx-coding-agent/blob/main/CHANGELOG.md" target="_blank" rel="noreferrer" className="btn-ghostmega">FULL CHANGELOG →</a>
        </div>
        <div className="mt-8 grid gap-3 md:grid-cols-3">
          {LOG.map((l, i) => (
            <motion.a
              key={l.v} href="https://github.com/OEvortex/vtx-coding-agent/blob/main/CHANGELOG.md" target="_blank" rel="noreferrer"
              initial={{ opacity: 0, y: 16 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }} transition={{ delay: i * 0.07 }}
              className="group rounded-2xl border border-white/10 bg-white/[0.02] p-5 hover:border-lime-400/40 transition-colors"
            >
              <div className="flex items-center justify-between">
                <span className="numeric text-xl font-bold text-white">{l.v}</span>
                <span className={`font-mono text-[10px] tracking-[0.18em] px-2 py-1 rounded-full ${l.t === "latest" ? "bg-lime-400 text-black font-bold" : "text-zinc-500 border border-white/10"}`}>{l.t.toUpperCase()}</span>
              </div>
              <p className="mt-3 text-[13px] leading-relaxed text-zinc-400 group-hover:text-zinc-300">{l.d}</p>
              <div className="mt-4 font-mono text-[11px] text-lime-300/70 group-hover:text-lime-300">read notes →</div>
            </motion.a>
          ))}
        </div>
      </div>
    </section>
  );
}
