import { useEffect, useRef } from "react";
import { animate } from "motion/react";
import { inr, reducedMotion } from "@/lib/utils";

/**
 * A rupee amount that counts up to its value when it first loads, and rolls to the new
 * value when it changes. Screen readers and reduced motion get the final figure at once.
 */
export function CountUp({ paise, whole = false, className, id }: { paise: number; whole?: boolean; className?: string; id?: string }) {
  const el = useRef<HTMLSpanElement>(null);
  const shown = useRef(0);
  useEffect(() => {
    const node = el.current;
    if (!node) return;
    if (reducedMotion()) {
      shown.current = paise;
      node.textContent = inr(paise, whole);
      return;
    }
    const controls = animate(shown.current, paise, {
      duration: 0.9,
      ease: [0.2, 0.9, 0.25, 1],
      onUpdate: (v) => {
        shown.current = v;
        node.textContent = inr(Math.round(v), whole);
      },
    });
    return () => controls.stop();
  }, [paise, whole]);
  return (
    <span id={id} className={className}>
      <span ref={el} aria-hidden>{inr(0, whole)}</span>
      <span className="sr-only">{inr(paise, whole)}</span>
    </span>
  );
}
