import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Mark } from "./Logo";
import { reducedMotion } from "@/lib/utils";

/** Every promise starts with the same U, so the U stays put and only the rest of the word rolls. */
const PHRASES: [string, string][] = [
  ["nbelievably", "fast."],
  ["nthinkably", "secure."],
  ["ndefeatably", "ahead."],
  ["nmistakably", "simple."],
  ["nbeatable", "Bank."],
];

export function WordRoller({ onSettled, step = 1500 }: { onSettled?: () => void; step?: number }) {
  const [i, setI] = useState(() => (reducedMotion() ? PHRASES.length - 1 : 0));
  const last = i === PHRASES.length - 1;

  useEffect(() => {
    if (last) {
      const t = setTimeout(() => onSettled?.(), 700);
      return () => clearTimeout(t);
    }
    const t = setTimeout(() => setI((n) => n + 1), i === 0 ? step + 600 : step);
    return () => clearTimeout(t);
  }, [i, last, step, onSettled]);

  const [head, tail] = PHRASES[i]!;
  return (
    <h1 className="font-display font-semibold tracking-[-0.035em] text-white leading-[0.98] text-[clamp(2.6rem,9.4vw,6.4rem)]">
      <span className="block text-white/55 text-[0.42em] tracking-[-0.01em] font-medium mb-[0.35em]">We are</span>
      <span className="sr-only">Unbelievably fast, unthinkably secure, undefeatably ahead: Unbeatable Bank.</span>
      <span aria-hidden className="flex items-start">
        <motion.span
          className="relative -ml-[0.06em] mr-[0.01em] mt-[0.02em] inline-block h-[0.9em] w-[0.74em]"
          animate={last ? { scale: [1, 1.12, 1], rotate: [0, -4, 0] } : { scale: 1 }}
          transition={{ duration: 0.55, ease: [0.2, 0.9, 0.25, 1] }}
        >
          <Mark tile={false} crop title={null} className="absolute inset-0 h-full w-full overflow-visible" />
          {last && (
            <motion.span
              className="absolute -inset-[0.3em] rounded-full bg-volt/40 blur-2xl"
              initial={{ opacity: 0.9, scale: 0.4 }}
              animate={{ opacity: 0, scale: 1.4 }}
              transition={{ duration: 0.9, ease: "easeOut" }}
            />
          )}
        </motion.span>
        <span className="relative inline-block overflow-hidden pb-[0.08em]">
          {/* Sizes the window to the longest phrase so nothing reflows while it rolls. */}
          <span className="invisible block whitespace-nowrap">ndefeatably<br />simple.</span>
          <AnimatePresence initial={false} mode="popLayout">
            <motion.span
              key={i}
              className="absolute inset-0 block whitespace-nowrap"
              initial={{ y: "105%", opacity: 0, filter: "blur(6px)" }}
              animate={{ y: "0%", opacity: 1, filter: "blur(0px)" }}
              exit={{ y: "-105%", opacity: 0, filter: "blur(6px)" }}
              transition={{ type: "spring", stiffness: 260, damping: 30, mass: 0.9 }}
            >
              {head}
              <br />
              <span className={last ? "text-volt" : "text-white"}>{tail}</span>
            </motion.span>
          </AnimatePresence>
        </span>
      </span>
    </h1>
  );
}
