import type { ReactNode } from "react";

/**
 * Section label: small mono caption with an optional accent dot.
 * Used very sparingly. Mechanical pre-flight: this component renders the
 * only `uppercase tracking` micro-labels on the page. Keep count small.
 */
export function SectionLabel({
  children,
  variant = "dim",
  index,
}: {
  children: ReactNode;
  variant?: "dim" | "bright" | "accent";
  index?: string;
}) {
  const palette = {
    dim: "text-ink-faint",
    bright: "text-ink-muted",
    accent: "text-accent",
  }[variant];

  return (
    <div className={`flex items-center gap-2.5 ${palette}`}>
      {index && (
        <span className="font-mono text-[10px] tracking-[0.18em] font-medium">
          {index}
        </span>
      )}
      {index && <span className="block w-1 h-1 rounded-full bg-current opacity-40" />}
      <span className="font-mono text-[10px] tracking-[0.18em] font-medium uppercase">
        {children}
      </span>
    </div>
  );
}
