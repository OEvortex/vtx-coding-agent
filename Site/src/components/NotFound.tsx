import { motion, useReducedMotion } from "motion/react";
import { ArrowLeft, Terminal } from "@phosphor-icons/react";

export default function NotFound() {
  const reduce = useReducedMotion();

  return (
    <div className="bg-canvas min-h-screen flex items-center justify-center px-5">
      <motion.div
        initial={reduce ? false : { opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
        className="text-center max-w-md"
      >
        <div className="inline-flex items-center justify-center w-16 h-16 rounded-xl bg-surface border border-hairline mb-6">
          <Terminal size={24} weight="duotone" className="text-accent" />
        </div>

        <h1 className="text-display text-ink text-[48px] sm:text-[64px] font-semibold mb-4">
          404
        </h1>

        <p className="text-[15px] text-ink-muted leading-[1.6] mb-8">
          This page doesn't exist. It may have been moved or the URL might be
          incorrect.
        </p>

        <div className="flex flex-col sm:flex-row items-center justify-center gap-3">
          <a
            href="/"
            className="btn-primary"
          >
            <ArrowLeft size={14} weight="bold" />
            <span>Back to home</span>
          </a>
          <a
            href="/docs/"
            className="btn-secondary"
          >
            <span>Read the docs</span>
          </a>
        </div>

        <div className="mt-12 p-4 bg-surface border border-hairline rounded-lg font-mono text-[12.5px] text-ink-faint">
          <span className="text-accent select-none">$ </span>
          <span>echo "Page not found"</span>
        </div>
      </motion.div>
    </div>
  );
}