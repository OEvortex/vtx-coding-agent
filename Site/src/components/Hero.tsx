import { motion, useReducedMotion } from "motion/react";
import { ArrowRight, Terminal } from "@phosphor-icons/react";
import TerminalDemo from "./TerminalDemo";

export default function Hero() {
  const reduce = useReducedMotion();
  const spring = { type: "spring" as const, stiffness: 100, damping: 20 };

  return (
    <section
      id="hero"
      className="relative bg-canvas pt-32 sm:pt-40 pb-20 sm:pb-28 px-5 sm:px-7 overflow-hidden"
    >
      {/* Ambient glows */}
      <div
        className="pointer-events-none absolute inset-0 -z-0"
        aria-hidden="true"
        style={{
          background:
            "radial-gradient(70% 50% at 18% 8%, rgba(163, 230, 53, 0.055), transparent 60%), radial-gradient(50% 40% at 85% 90%, rgba(96, 165, 250, 0.03), transparent 55%)",
        }}
      />
      {/* Faint blueprint grid */}
      <div
        className="pointer-events-none absolute inset-0 -z-0 opacity-[0.35]"
        aria-hidden="true"
        style={{
          backgroundImage:
            "linear-gradient(rgba(250, 250, 249, 0.022) 1px, transparent 1px), linear-gradient(90deg, rgba(250, 250, 249, 0.022) 1px, transparent 1px)",
          backgroundSize: "72px 72px",
          maskImage: "radial-gradient(75% 60% at 50% 30%, black 30%, transparent 100%)",
          WebkitMaskImage: "radial-gradient(75% 60% at 50% 30%, black 30%, transparent 100%)",
        }}
      />

      <div className="relative max-w-[1400px] mx-auto">
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-12 lg:gap-10 items-center min-h-[520px]">
          {/* Copy */}
          <div className="lg:col-span-6 flex flex-col items-start text-left max-w-[620px]">
            <motion.div
              initial={reduce ? false : { opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ ...spring, delay: 0.05 }}
              className="chip-accent"
            >
              <span className="dot-pulse" style={{ width: 5, height: 5 }} />
              v1.0 — mono-repo release
            </motion.div>

            <motion.h1
              initial={reduce ? false : { opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ ...spring, delay: 0.15 }}
              className="text-display text-ink text-[44px] sm:text-[60px] lg:text-[68px] font-semibold leading-[0.95] mt-6"
            >
              Your coding agent,
              <br />
              <span className="text-sheen">minus the prompt bloat.</span>
            </motion.h1>

            <motion.p
              initial={reduce ? false : { opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ ...spring, delay: 0.3 }}
              className="mt-6 text-[16px] sm:text-[17px] text-ink-muted max-w-[46ch] leading-[1.65]"
            >
              Vtx runs a transparent agent loop on a ~2,600-token runtime —
              so your context window spends itself on your code, not hidden
              instructions. Nine surgical tools, 50+ providers, TUI and
              headless CLI.
            </motion.p>

            <motion.div
              initial={reduce ? false : { opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ ...spring, delay: 0.4 }}
              className="mt-9 flex flex-wrap items-center gap-3"
            >
              <a
                href="https://github.com/OEvortex/vtx-coding-agent"
                target="_blank"
                rel="noopener noreferrer"
                className="btn-primary"
              >
                <span>Get started</span>
                <ArrowRight size={14} weight="bold" />
              </a>
              <a href="/docs/" className="btn-secondary">
                <Terminal size={14} weight="regular" />
                <span>Read the docs</span>
              </a>
            </motion.div>

            <motion.div
              initial={reduce ? false : { opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ ...spring, delay: 0.55 }}
              className="mt-6 flex items-center gap-3 text-[12px] text-ink-faint font-mono"
            >
              <span>$</span>
              <code className="bg-surface/60 px-2 py-1 rounded border border-hairline">
                uv tool install vtx-coding-agent
              </code>
            </motion.div>
          </div>

          {/* Live terminal */}
          <motion.div
            initial={reduce ? false : { opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ ...spring, delay: 0.35 }}
            className="lg:col-span-6 w-full"
          >
            <TerminalDemo />
          </motion.div>
        </div>
      </div>
    </section>
  );
}
