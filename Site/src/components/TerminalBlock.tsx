import type { ReactNode } from "react";
import { CornersIn } from "@phosphor-icons/react";

export function TerminalBlock({
  title = "vtx",
  children,
  className = "",
}: {
  title?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`relative bg-[#0A0A0B] border border-hairline rounded-xl overflow-hidden group shadow-2xl ${className}`}
    >
      {/* Subtle inner glow */}
      <div
        className="absolute inset-0 pointer-events-none"
        aria-hidden="true"
        style={{
          background: "linear-gradient(180deg, rgba(163, 230, 53, 0.02) 0%, transparent 20%)",
        }}
      />

      {/* Title bar */}
      <div className="flex items-center justify-between px-4 h-10 border-b border-hairline bg-surface/80">
        <div className="flex items-center gap-2">
          <span className="block w-3 h-3 rounded-full bg-[#EF4444]/80" />
          <span className="block w-3 h-3 rounded-full bg-[#F59E0B]/80" />
          <span className="block w-3 h-3 rounded-full bg-[#22C55E]/80" />
        </div>
        <div className="flex items-center gap-2 text-ink-faint">
          <CornersIn size={12} weight="bold" />
          <span className="font-mono text-[11px] tracking-tight">{title}</span>
        </div>
        <div className="w-14" />
      </div>

      {/* Body */}
      <div className="font-mono text-[12.5px] leading-[1.7] text-zinc-300 px-5 py-4">
        {children}
      </div>
    </div>
  );
}

export function TermLine({
  prompt = "$",
  cmd,
  out,
  promptColor = "text-accent",
}: {
  prompt?: string;
  cmd: string;
  out?: ReactNode;
  promptColor?: string;
}) {
  return (
    <div className="space-y-1">
      <div>
        <span className={`${promptColor} select-none`}>{prompt} </span>
        <span className="text-zinc-100">{cmd}</span>
      </div>
      {out && <div className="text-zinc-500 pl-4">{out}</div>}
    </div>
  );
}
