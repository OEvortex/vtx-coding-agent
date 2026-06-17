import { motion, useScroll, useSpring, useReducedMotion } from "motion/react";

export default function ScrollProgress() {
  const { scrollYProgress } = useScroll();
  const reduce = useReducedMotion();
  const x = useSpring(scrollYProgress, { stiffness: 200, damping: 30 });

  if (reduce) return null;

  return (
    <motion.div
      className="fixed top-0 left-0 right-0 h-[2px] origin-left z-50"
      style={{
        scaleX: x,
        background: "linear-gradient(90deg, #A3E635 0%, #BEF264 100%)",
        boxShadow: "0 0 12px rgba(163, 230, 53, 0.5)",
      }}
    />
  );
}
