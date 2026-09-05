import { motion, useReducedMotion } from "motion/react";
import { ArrowRight, Lightning, Copy, Check } from "@phosphor-icons/react";
import { useState } from "react";
import ParticleStorm from "./ParticleStorm";
import TerminalDemo from "./TerminalDemo";

function useCopy(text: string) {
  const [ok, setOk] = useState(false);
  return {
    ok,
    copy: async () => {
      try {
        await navigator.clipboard.writeText(text);
      } catch {
        const ta = document.createElement("textarea");
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        ta.remove();
      }
      setOk(true);
      setTimeout(() => setOk(false), 1400);
    },
  };
}

export default function HeroInsane() {
  const reduce = useReducedMotion();
  const { ok, copy } = useCopy("uv tool install vtx-coding-agent");
  const cmd = "uv tool install vtx-coding-agent";

  return (
    <section id="hero" className="relative overflow-hidden bg-[#070708] pt-28 sm:pt-36 pb-10 px-4 sm:px-8">
      {/* aurora blobs */}
      <div className="aurora-blob aurora-a" aria-hidden="true" />
      <div className="aurora-blob aurora-b" aria-hidden="true" />
      <div className="aurora-blob aurora-c" aria-hidden="true" />
      <div className="hero-grid" aria-hidden="true" />
      <div className="absolute inset-0">
        <ParticleStorm />
      </div>
      <div className="vtx-huge" aria-hidden="true">VTX◦VTX◦VTX</div>

      <div className="relative mx-auto max-w-[1440px]">
        {/* top ticker */}
        <motion.div
          initial={reduce ? false : { opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className="mb-6 flex flex-wrap items-center gap-2 font-mono text-[11px]"
        >
          <span className="live-pill"><span className="dot-pulse" /> LIVE · v1.1.1 · open-source · apache-2.0</span>
          <span className="live-pill dim">~2,600 tokens runtime</span>
          <span className="live-pill dim hidden sm:inline-flex">50+ providers</span>
          <span className="live-pill lime hidden md:inline-flex"><Lightning size={12} weight="fill" /> context is weapon</span>
        </motion.div>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-10 items-end">
          <div className="lg:col-span-7">
            <motion.h1
              initial={reduce ? false : { opacity: 0, y: 28 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ type: "spring", stiffness: 90, damping: 20 }}
              className="insane-title"
            >
              <span className="block text-zinc-100">YOUR AGENT</span>
              <span className="block title-stroke">EATS CONTEXT</span>
              <span className="block title-lime">VTX STARVES IT<span className="title-caret">▌</span></span>
            </motion.h1>

            <motion.p
              initial={reduce ? false : { opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.15 }}
              className="mt-6 max-w-[52ch] text-[15px] sm:text-[17px] leading-[1.7] text-zinc-400"
            >
              Most harnesses burn <span className="text-zinc-200 font-semibold">10k+ hidden tokens</span> before
              you type. <span className="text-lime-300 font-semibold">Vtx runs the whole loop — prompt + tools +
              env — in ~2.6k.</span> More context for your code. Cheaper turns. A prompt you can actually audit.
              TUI + headless CLI + Python SDK.
            </motion.p>

            <div className="mt-8 flex flex-wrap items-center gap-3">
              <a href="https://github.com/OEvortex/vtx-coding-agent" target="_blank" rel="noreferrer" className="btn-mega">
                <span>STAR & SEND IT</span><ArrowRight size={16} weight="bold" />
              </a>
              <a href="/docs/" className="btn-ghostmega">READ THE DOCS →</a>
              <button onClick={copy} className="copy-chip" aria-label="copy install command">
                <span className="text-lime-300">$</span><code>{cmd}</code>
                {ok ? <Check size={14} className="text-lime-300" /> : <Copy size={14} className="text-zinc-500" />}
              </button>
            </div>

            {/* token bar */}
            <div className="mt-8 grid grid-cols-3 max-w-[560px] overflow-hidden rounded-xl border border-white/10 bg-white/[0.02]">
              {[
                ["VTX", "2.6k", "lime"],
                ["OTHERS", "12k+", "red"],
                ["SAVED", "~78%", "cyan"],
              ].map(([k, v, c]) => (
                <div key={k} className="px-4 py-3 border-r last:border-0 border-white/10">
                  <div className="font-mono text-[10px] tracking-[0.2em] text-zinc-500">{k}</div>
                  <div className={`numeric text-2xl font-bold ${c === "lime" ? "text-lime-300" : c === "red" ? "text-red-400 line-through" : "text-cyan-300"}`}>{v}</div>
                </div>
              ))}
            </div>
          </div>

          <motion.div
            initial={reduce ? false : { opacity: 0, y: 30, rotateX: 8 }}
            animate={{ opacity: 1, y: 0, rotateX: 0 }}
            transition={{ delay: 0.2, type: "spring", stiffness: 70, damping: 18 }}
            className="lg:col-span-5 tilt-wrap"
          >
            <div className="terminal-3d">
              <TerminalDemo />
            </div>
            <div className="mt-3 flex items-center justify-between font-mono text-[10.5px] text-zinc-600">
              <span>● REC · live agent loop · prompt mode</span>
              <span>shift+tab → switch agent</span>
            </div>
          </motion.div>
        </div>

        {/* bottom marquee */}
        <div className="mega-marquee mt-12" aria-hidden="true">
          <div className="mega-track">
            {Array.from({ length: 2 }).map((_, i) => (
              <span key={i} className="mega-chunk">
                READ ✦ EDIT ✦ WRITE ✦ BASH ✦ FIND ✦ SKILL ✦ WEB ✦ ASK_USER ✦ TASK ✦ GOAL ✦&nbsp;
              </span>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
