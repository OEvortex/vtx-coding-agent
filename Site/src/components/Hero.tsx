import { motion, useReducedMotion } from "motion/react";
import { ArrowRight, GithubLogo, Terminal } from "@phosphor-icons/react";
import { TerminalBlock, TermLine } from "./TerminalBlock";
import { SectionLabel } from "./SectionLabel";

export default function Hero() {
  const reduce = useReducedMotion();
  const spring = { type: "spring" as const, stiffness: 100, damping: 20 };

  return (
    <section
      id="hero"
      className="relative bg-canvas pt-32 sm:pt-40 pb-20 sm:pb-28 px-5 sm:px-7 overflow-hidden"
    >
      {/* Ambient glow effects */}
      <div
        className="pointer-events-none absolute inset-0 -z-0"
        aria-hidden="true"
        style={{
          background:
            "radial-gradient(80% 50% at 20% 20%, rgba(163, 230, 53, 0.06), transparent 60%), radial-gradient(60% 40% at 80% 80%, rgba(96, 165, 250, 0.03), transparent 50%)",
        }}
      />

      <div className="relative max-w-[1400px] mx-auto">
        <div className="flex flex-col items-center text-center max-w-[800px] mx-auto">
          <motion.h1
            initial={reduce ? false : { opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ ...spring, delay: 0.15 }}
            className="text-display text-ink text-[48px] sm:text-[64px] lg:text-[80px] font-semibold leading-[0.95]"
          >
            Your coding agent
            <br />
            <span className="text-ink-muted">for the terminal.</span>
          </motion.h1>

          <motion.p
            initial={reduce ? false : { opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ ...spring, delay: 0.3 }}
            className="mt-7 text-[16px] sm:text-[17px] text-ink-muted max-w-[48ch] leading-[1.65]"
          >
            Vtx is a minimal, transparent agent loop for developers. Nine core
            tools, eleven LLM providers, sessions, compaction, skills, and a
            full extension system.
          </motion.p>

          <motion.div
            initial={reduce ? false : { opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ ...spring, delay: 0.4 }}
            className="mt-9 flex flex-wrap items-center justify-center gap-3"
          >
            <a
              href="https://github.com/OEvortex/vtx-coding-agent"
              target="_blank"
              rel="noopener noreferrer"
              className="btn-primary"
            >
              <span>Install Vtx</span>
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
            className="mt-6 flex items-center justify-center gap-4 text-[12px] text-ink-faint font-mono"
          >
            <span className="flex items-center gap-2">
              <span className="text-ink-muted">$</span>
              <span className="bg-surface/50 px-2 py-1 rounded border border-hairline">uv tool install vtx-coding-agent</span>
            </span>
          </motion.div>
        </div>
      </div>
    </section>
  );
}
