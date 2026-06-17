import { ArrowRight, Copy, Check } from "@phosphor-icons/react";
import { useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { Reveal } from "./Reveal";
import { SectionLabel } from "./SectionLabel";

const installCmd = "uv tool install vtx-coding-agent";

export default function CTASection() {
  const [copied, setCopied] = useState(false);
  const reduce = useReducedMotion();

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(installCmd);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // ignore
    }
  };

  return (
    <section className="bg-canvas border-t border-hairline py-24 sm:py-32 px-5 sm:px-7">
      <div className="max-w-[1400px] mx-auto">
        <motion.div
          initial={reduce ? false : { opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.3 }}
          transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
          className="relative border border-hairline rounded-xl overflow-hidden bg-surface"
        >
          {/* Soft accent glow. */}
          <div
            className="pointer-events-none absolute -top-32 right-0 w-[500px] h-[500px] opacity-50"
            aria-hidden="true"
            style={{
              background:
                "radial-gradient(closest-side, rgba(163, 230, 53, 0.18), transparent 70%)",
            }}
          />
          {/* Subtle grid with dot pattern. */}
          <div
            className="pointer-events-none absolute inset-0 opacity-[0.04]"
            aria-hidden="true"
            style={{
              backgroundImage:
                "radial-gradient(rgba(250,250,249,0.8) 1px, transparent 1px)",
              backgroundSize: "24px 24px",
            }}
          />

          <div className="relative grid grid-cols-1 lg:grid-cols-12 gap-8 p-8 sm:p-12 lg:p-14 items-center">
            <div className="lg:col-span-7">
              <Reveal>
                <SectionLabel variant="accent" index="05">
                  Get started
                </SectionLabel>
              </Reveal>
              <Reveal delay={0.05}>
                <h2 className="mt-5 text-display text-ink text-[36px] sm:text-[44px] lg:text-[60px] font-semibold">
                  Install once.
                  <br />
                  <span className="text-ink-muted">Use it forever.</span>
                </h2>
              </Reveal>
              <Reveal delay={0.1}>
                <p className="mt-5 text-[15px] text-ink-muted max-w-[44ch] leading-[1.6]">
                  One line. No accounts. No telemetry. The whole harness is
                  on your machine, MIT licensed, and ready in under 30 seconds.
                </p>
              </Reveal>
            </div>

            <div className="lg:col-span-5 flex flex-col gap-4">
              <Reveal delay={0.1}>
                <div className="bg-canvas border border-hairline rounded-xl p-5 font-mono text-[13px]">
                  <div className="flex items-center gap-2 text-ink-faint mb-3">
                    <span className="block w-1.5 h-1.5 rounded-full bg-accent" />
                    <span className="text-[10.5px] tracking-[0.18em] uppercase">
                      Terminal
                    </span>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <code className="text-ink truncate">
                      <span className="text-accent select-none">$ </span>
                      {installCmd}
                    </code>
                    <button
                      onClick={handleCopy}
                      className="shrink-0 p-1.5 rounded-md text-ink-faint hover:text-ink hover:bg-surface-2 transition-colors"
                      aria-label="Copy install command"
                    >
                      {copied ? (
                        <Check size={14} weight="bold" className="text-accent" />
                      ) : (
                        <Copy size={14} weight="regular" />
                      )}
                    </button>
                  </div>
                </div>
              </Reveal>
              <Reveal delay={0.15}>
                <div className="flex flex-wrap gap-3">
                  <a
                    href="https://github.com/OEvortex/vtx-coding-agent"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="btn-primary"
                  >
                    <span>View on GitHub</span>
                    <ArrowRight size={14} weight="bold" />
                  </a>
                  <a href="/docs/" className="btn-secondary">
                    <span>Read the docs</span>
                  </a>
                </div>
              </Reveal>
            </div>
          </div>
        </motion.div>
      </div>
    </section>
  );
}
