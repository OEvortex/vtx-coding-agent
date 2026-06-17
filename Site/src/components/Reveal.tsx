import { forwardRef } from "react";
import { motion, useReducedMotion, type MotionProps } from "motion/react";
import type { ReactNode, ComponentProps } from "react";

interface RevealProps extends Omit<MotionProps, "children"> {
  children: ReactNode;
  className?: string;
  delay?: number;
  y?: number;
  as?: "div" | "section" | "article" | "li" | "span";
  once?: boolean;
  amount?: number;
  onMouseMove?: (e: React.MouseEvent<HTMLDivElement>) => void;
}

export const Reveal = forwardRef<HTMLDivElement, RevealProps>(
  function Reveal(
    {
      children,
      className,
      delay = 0,
      y = 20,
      as = "div",
      once = true,
      amount = 0.2,
      onMouseMove,
      ...rest
    },
    ref
  ) {
    const reduce = useReducedMotion();

    if (reduce) {
      const Tag = as as "div";
      return (
        <Tag ref={ref} className={className} onMouseMove={onMouseMove}>
          {children}
        </Tag>
      );
    }

    const MotionTag = motion[as] as typeof motion.div;

    return (
      <MotionTag
        ref={ref}
        className={className}
        initial={{ opacity: 0, y }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once, amount }}
        transition={{ duration: 0.6, delay, ease: [0.16, 1, 0.3, 1] }}
        onMouseMove={onMouseMove}
        {...rest}
      >
        {children}
      </MotionTag>
    );
  }
);
