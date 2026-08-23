import { useEffect, useState } from "react";
import { useReducedMotion } from "motion/react";
import { CornersIn } from "@phosphor-icons/react";

/**
 * Self-playing terminal transcript of a real Vtx session.
 * Types the user command, streams tool calls, pauses, loops.
 * Static under prefers-reduced-motion.
 */

type LineKind = "cmd" | "tool" | "ok" | "say" | "sys";

interface ScriptLine {
  kind: LineKind;
  text: string;
}

const SCRIPT: ScriptLine[] = [
  { kind: "cmd", text: "refactor the provider registry to load lazily" },
  { kind: "sys", text: "vtx v1.0.0 · 9 tools · prompt mode · ~2.6k tokens loaded" },
  { kind: "tool", text: "read   src/ai/providers/registry.py          142 lines" },
  { kind: "tool", text: "edit   src/ai/providers/registry.py          +24 -31" },
  { kind: "tool", text: "bash   uv run pytest tests/test_providers.py -q" },
  { kind: "ok", text: "14 passed in 1.82s" },
  { kind: "say", text: "Registry now resolves providers on first use instead of import." },
  { kind: "say", text: "Startup cost drops by 3 eager imports; catalog API unchanged." },
];

function LineView({ line }: { line: ScriptLine }) {
  switch (line.kind) {
    case "cmd":
      return (
        <div className="flex gap-2">
          <span className="text-accent select-none shrink-0">❯</span>
          <span className="text-zinc-100">{line.text}</span>
        </div>
      );
    case "sys":
      return <div className="text-zinc-600 pl-4">{line.text}</div>;
    case "tool":
      return (
        <div className="text-zinc-500 pl-4 flex gap-2">
          <span className="w-10 shrink-0 text-zinc-600">{line.text.slice(0, 5)}</span>
          <span className="truncate">{line.text.slice(7)}</span>
        </div>
      );
    case "ok":
      return (
        <div className="pl-4 text-emerald-400">{line.text}</div>
      );
    case "say":
      return <div className="text-zinc-300 pl-4">{line.text}</div>;
  }
}

export default function TerminalDemo() {
  const reduce = useReducedMotion();
  const [lineIdx, setLineIdx] = useState(0);
  const [charIdx, setCharIdx] = useState(0);

  useEffect(() => {
    if (reduce) {
      setLineIdx(SCRIPT.length);
      return;
    }
    const line = SCRIPT[lineIdx];
    if (!line) {
      // End of script: hold, then restart.
      const t = setTimeout(() => {
        setLineIdx(0);
        setCharIdx(0);
      }, 5200);
      return () => clearTimeout(t);
    }
    if (line.kind === "cmd") {
      if (charIdx < line.text.length) {
        const t = setTimeout(() => setCharIdx((c) => c + 1), 32 + Math.random() * 40);
        return () => clearTimeout(t);
      }
      const t = setTimeout(() => {
        setLineIdx((i) => i + 1);
        setCharIdx(0);
      }, 480);
      return () => clearTimeout(t);
    }
    const t = setTimeout(
      () => {
        setLineIdx((i) => i + 1);
        setCharIdx(0);
      },
      line.kind === "tool" ? 560 : 700,
    );
    return () => clearTimeout(t);
  }, [lineIdx, charIdx, reduce]);

  const done = reduce || lineIdx >= SCRIPT.length;
  const current = !done && SCRIPT[lineIdx];
  const isTypingCmd = current?.kind === "cmd";

  return (
    <div className="relative">
      {/* Glow behind the window */}
      <div
        className="pointer-events-none absolute -inset-8 -z-10 opacity-70"
        aria-hidden="true"
        style={{
          background:
            "radial-gradient(60% 55% at 50% 40%, rgba(163, 230, 53, 0.07), transparent 70%)",
        }}
      />
      <div className="relative bg-[#0C0C0E] border border-hairline-strong rounded-xl overflow-hidden shadow-[0_24px_80px_-24px_rgba(0,0,0,0.8)]">
        {/* Title bar */}
        <div className="flex items-center justify-between px-4 h-10 border-b border-hairline bg-surface/70">
          <div className="flex items-center gap-2">
            <span className="block w-2.5 h-2.5 rounded-full bg-[#EF4444]/70" />
            <span className="block w-2.5 h-2.5 rounded-full bg-[#F59E0B]/70" />
            <span className="block w-2.5 h-2.5 rounded-full bg-[#22C55E]/70" />
          </div>
          <div className="flex items-center gap-2 text-ink-faint">
            <CornersIn size={12} weight="bold" />
            <span className="font-mono text-[11px] tracking-tight">vtx — zsh</span>
          </div>
          <div className="w-12" />
        </div>

        {/* Transcript */}
        <div className="font-mono text-[12px] sm:text-[12.5px] leading-[1.85] px-4 sm:px-5 py-4 h-[300px] overflow-hidden">
          {SCRIPT.slice(0, done ? SCRIPT.length : lineIdx).map((l, i) => (
            <LineView key={i} line={l} />
          ))}
          {!done && isTypingCmd && (
            <div className="flex gap-2">
              <span className="text-accent select-none shrink-0">❯</span>
              <span className="text-zinc-100">
                {current!.text.slice(0, charIdx)}
                <span className="term-caret ml-0.5" aria-hidden="true" />
              </span>
            </div>
          )}
          {!done && !isTypingCmd && (
            <div className="flex gap-2">
              <span className="text-accent select-none shrink-0">❯</span>
              <span className="term-caret" aria-hidden="true" />
            </div>
          )}
          {done && (
            <div className="flex gap-2 mt-1">
              <span className="text-accent select-none shrink-0">❯</span>
              <span className="term-caret" aria-hidden="true" />
            </div>
          )}
        </div>

        {/* Status bar */}
        <div className="flex items-center justify-between px-4 h-8 border-t border-hairline bg-surface/50 font-mono text-[10px] tracking-wide text-ink-faint">
          <span className="flex items-center gap-1.5">
            <span className="block w-1.5 h-1.5 rounded-full bg-accent dot-pulse" />
            agent idle
          </span>
          <span>utf-8 · bash</span>
        </div>
      </div>
    </div>
  );
}
