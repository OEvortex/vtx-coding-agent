import { useEffect, useRef, useState, type ReactNode } from "react";

/**
 * Marquee: a single horizontal-scrolling band.
 * Per Section 5 of the taste skill, max one marquee per page.
 * Pauses on hover. Disabled under prefers-reduced-motion.
 */
export function Marquee({
  children,
  speed = 38,
  className = "",
}: {
  children: ReactNode;
  speed?: number;
  className?: string;
}) {
  const [reduce, setReduce] = useState(false);
  const trackRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduce(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);

  return (
    <div className={`relative overflow-hidden ${className}`}>
      <div
        ref={trackRef}
        className="marquee-track"
        style={reduce ? { animation: "none" } : { animationDuration: `${speed}s` }}
      >
        <div className="flex shrink-0">{children}</div>
        <div className="flex shrink-0" aria-hidden="true">
          {children}
        </div>
      </div>
      {/* Edge fades so text doesn't slam into the boundary. */}
      <div className="pointer-events-none absolute inset-y-0 left-0 w-24 bg-gradient-to-r from-[#0A0A0B] to-transparent" />
      <div className="pointer-events-none absolute inset-y-0 right-0 w-24 bg-gradient-to-l from-[#0A0A0B] to-transparent" />
    </div>
  );
}
