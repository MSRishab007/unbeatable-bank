import { forwardRef, useState } from "react";
import { AnimatePresence, motion, useAnimationControls } from "motion/react";
import { haptic } from "@/lib/motion";
import { cn } from "@/lib/utils";

/**
 * The amount, the way payment apps show it: big digits that slide in as they're typed,
 * Indian grouping (1,25,000) added as you go, a thick accent cursor in place of the thin
 * text caret, and a size that steps down as the number grows so it always fits. A key that
 * can't be part of an amount gives a small shake instead of doing nothing.
 *
 * Underneath is an ordinary <input id="amount">, so typing, pasting, the phone's number pad,
 * screen readers and tests all work as usual; it is simply drawn invisibly on top.
 */
const MAX_RUPEE_DIGITS = 7;

function clean(next: string): string | null {
  const v = next.replace(/[,\s₹]/g, "");
  if (v === "") return "";
  if (!/^\d*\.?\d{0,2}$/.test(v)) return null;
  const [whole = ""] = v.split(".");
  if (whole.replace(/^0+(?=\d)/, "").length > MAX_RUPEE_DIGITS) return null;
  return v.replace(/^0+(?=\d)/, "");
}

function grouped(v: string): string {
  const [whole = "", frac] = v.split(".");
  const last3 = whole.slice(-3);
  const rest = whole.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ",");
  return (rest ? `${rest},${last3}` : last3) + (frac !== undefined ? `.${frac}` : "");
}

export const AmountEntry = forwardRef<HTMLInputElement, { value: string; onChange: (v: string) => void; label?: string }>(
  function AmountEntry({ value, onChange, label = "Amount" }, ref) {
    const [focused, setFocused] = useState(false);
    const shake = useAnimationControls();
    const shown = value ? grouped(value) : "";
    const size = shown.length <= 4 ? "text-[60px]" : shown.length <= 7 ? "text-[52px]" : shown.length <= 9 ? "text-[44px]" : "text-[38px]";

    function refuse() {
      haptic(18);
      shake.start({ x: [0, -10, 9, -6, 4, 0], transition: { duration: 0.32 } });
    }

    return (
      <div className="relative mx-auto w-full max-w-[320px]">
        <motion.div animate={shake} className={cn("flex items-baseline justify-center font-display font-semibold leading-none tracking-[-0.04em] num", size)}
          aria-hidden>
          <span className={cn("mr-1 text-[0.55em] transition-colors duration-base", value ? "text-ink" : "text-muted")}>₹</span>
          <span className="relative inline-flex">
            {shown ? (
              <AnimatePresence initial={false} mode="popLayout">
                {[...shown].map((ch, k) => (
                  <motion.span key={`${k}-${ch}`} layout="position" initial={{ y: "0.45em", opacity: 0, scale: 0.85 }} animate={{ y: 0, opacity: 1, scale: 1 }}
                    exit={{ y: "-0.3em", opacity: 0, scale: 0.85 }} transition={{ type: "spring", stiffness: 620, damping: 32 }}
                    className={ch === "," ? "text-muted" : undefined}>
                    {ch}
                  </motion.span>
                ))}
              </AnimatePresence>
            ) : <span className="text-muted">0</span>}
            {focused && <span className="amount-caret ml-1 w-[4px] self-stretch rounded-full bg-accent" />}
          </span>
        </motion.div>
        <input ref={ref} id="amount" name="amount" inputMode="decimal" autoComplete="off" aria-label={label}
          value={value}
          onFocus={() => setFocused(true)} onBlur={() => setFocused(false)}
          onKeyDown={(e) => { if (e.key.length === 1 && !/[\d.]/.test(e.key) && !e.metaKey && !e.ctrlKey) { e.preventDefault(); refuse(); } }}
          onChange={(e) => { const v = clean(e.target.value); if (v === null) refuse(); else onChange(v); }}
          className="absolute inset-0 size-full cursor-text rounded-chip bg-transparent text-transparent caret-transparent outline-none selection:bg-transparent focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent" />
      </div>
    );
  },
);
