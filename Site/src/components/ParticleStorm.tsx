import { useEffect, useRef } from "react";

export default function ParticleStorm() {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current!;
    const ctx = canvas.getContext("2d")!;
    let w = 0;
    let h = 0;
    let raf = 0;
    const DPR = Math.min(window.devicePixelRatio || 1, 2);
    const mouse = { x: -9999, y: -9999 };
    type P = { x: number; y: number; vx: number; vy: number; r: number; hue: number };
    let pts: P[] = [];

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      w = rect.width;
      h = rect.height;
      canvas.width = w * DPR;
      canvas.height = h * DPR;
      ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
      const n = Math.min(140, Math.floor((w * h) / 14000));
      pts = Array.from({ length: n }, () => ({
        x: Math.random() * w,
        y: Math.random() * h,
        vx: (Math.random() - 0.5) * 0.35,
        vy: (Math.random() - 0.5) * 0.35,
        r: Math.random() * 1.6 + 0.4,
        hue: Math.random(),
      }));
    };
    resize();
    window.addEventListener("resize", resize);
    const onMove = (e: PointerEvent) => {
      const rect = canvas.getBoundingClientRect();
      mouse.x = e.clientX - rect.left;
      mouse.y = e.clientY - rect.top;
    };
    window.addEventListener("pointermove", onMove);

    const tick = () => {
      ctx.clearRect(0, 0, w, h);
      // links
      for (let i = 0; i < pts.length; i++) {
        const p = pts[i];
        p.x += p.vx;
        p.y += p.vy;
        const dxm = p.x - mouse.x;
        const dym = p.y - mouse.y;
        const dm = Math.hypot(dxm, dym);
        if (dm < 140 && dm > 1) {
          p.x += (dxm / dm) * 0.6;
          p.y += (dym / dm) * 0.6;
        }
        if (p.x < 0) p.x = w;
        if (p.x > w) p.x = 0;
        if (p.y < 0) p.y = h;
        if (p.y > h) p.y = 0;
        for (let j = i + 1; j < pts.length; j++) {
          const q = pts[j];
          const dx = p.x - q.x;
          const dy = p.y - q.y;
          const d = Math.hypot(dx, dy);
          if (d < 110) {
            ctx.strokeStyle = `rgba(163,230,53,${(1 - d / 110) * 0.22})`;
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(p.x, p.y);
            ctx.lineTo(q.x, q.y);
            ctx.stroke();
          }
        }
      }
      for (const p of pts) {
        const c = p.hue > 0.85 ? "190,242,100" : p.hue > 0.7 ? "103,232,249" : "163,230,53";
        ctx.fillStyle = `rgba(${c},0.9)`;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fill();
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
      window.removeEventListener("pointermove", onMove);
    };
  }, []);

  return <canvas ref={ref} className="absolute inset-0 h-full w-full opacity-70" aria-hidden="true" />;
}
