import { useEffect, useRef, useState } from "react";
import { motion, useReducedMotion, useInView } from "motion/react";

interface Stat {
  value: number;
  suffix?: string;
  label: string;
  detail: string;
}

const stats: Stat[] = [
  { value: 9, label: "Core tools", detail: "read, edit, write, bash, find..." },
  { value: 11, suffix: "+", label: "LLM providers", detail: "OAuth + local model support" },
  { value: 2.2, suffix: "k", label: "Base prompt", detail: "tokens loaded per turn" },
];

function Counter({ stat, index }: { stat: Stat; index: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, amount: 0.4 });
  const reduce = useReducedMotion();
  const [n, setN] = useState(0);
  const isFloat = !Number.isInteger(stat.value);

  useEffect(() => {
    if (!inView) return;
    if (reduce) {
      setN(stat.value);
      return;
    }
    const start = performance.now();
    const duration = 1200;
    const tick = (t: number) => {
      const p = Math.min((t - start) / duration, 1);
      const eased = 1 - Math.pow(1 - p, 3);
      setN(eased * stat.value);
      if (p < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }, [inView, stat.value, reduce]);

  const display = isFloat ? n.toFixed(1) : Math.round(n).toString();

  return (
    <motion.div
      ref={ref}
      initial={reduce ? false : { opacity: 0, y: 16 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.4 }}
      transition={{ duration: 0.5, delay: index * 0.08, ease: [0.16, 1, 0.3, 1] }}
      className="flex flex-col gap-3 py-8 px-6 sm:px-8 border-l border-hairline first:border-l-0 first:pl-0"
    >
      <span className="numeric text-ink text-[48px] sm:text-[60px] font-semibold">
        {display}
        {stat.suffix && <span className="text-accent">{stat.suffix}</span>}
      </span>
      <div className="space-y-1">
        <p className="text-[14px] text-ink font-medium tracking-tight">
          {stat.label}
        </p>
        <p className="text-[12px] text-ink-faint font-mono leading-snug">
          {stat.detail}
        </p>
      </div>
    </motion.div>
  );
}

export default function LiveStats() {
  return (
    <section className="bg-canvas border-t border-hairline relative">
      <div
        className="absolute inset-0 pointer-events-none"
        aria-hidden="true"
        style={{
          background:
            "radial-gradient(ellipse at 50% 0%, rgba(163, 230, 53, 0.03) 0%, transparent 60%)",
        }}
      />
      <div className="relative max-w-[1400px] mx-auto px-5 sm:px-7">
        <div className="grid grid-cols-2 lg:grid-cols-3 divide-x divide-hairline">
          {stats.map((s, i) => (
            <Counter key={s.label} stat={s} index={i} />
          ))}
        </div>
      </div>
    </section>
  );
}
