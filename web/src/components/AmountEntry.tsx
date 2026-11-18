import { forwardRef, useCallback, useRef, useState } from "react";
import { AnimatePresence, motion, useAnimationControls } from "motion/react";
import { haptic } from "@/lib/motion";
import { rupeesInWords } from "@/lib/words";
import { cn } from "@/lib/utils";

/**
 * The amount, the way UPI apps show it: a light, large number, a thin cursor that always
 * stays at the end, Indian grouping (1,25,000), and the amount in words underneath
 * ("Rupees One Thousand Only") so a slip of the finger is easy to see.
 *
 * The cursor can't be moved: arrow keys, clicks and selections all put it back at the end,
 * so typing can only add to or remove from the end (no "1", left arrow, "2" giving 21).
 * With nothing entered it sits before the grey 0; after the first digit, after the number.
 *
 * Underneath is an ordinary <input id="amount">, so the phone's number pad, paste, screen
 * readers and tests work as usual; it is drawn invisibly on top.
 */
const MAX_RUPEE_DIGITS = 7;
const NAV_KEYS = new Set(["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown"]);

function clean(next: string): string | null {
  const v = next.replace(/[,\s₹]/g, "");
  if (v === "") return "";
  if (!/^\d*\.?\d{0,2}$/.test(v)) return null;
  const whole = (v.split(".")[0] ?? "").replace(/^0+(?=\d)/, "");
  if (whole.length > MAX_RUPEE_DIGITS) return null;
  return v.startsWith(".") ? `0${v}` : v.replace(/^0+(?=\d)/, "");
}

function grouped(v: string): string {
  const [whole = "", frac] = v.split(".");
  const last3 = whole.slice(-3);
  const rest = whole.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ",");
  return (rest ? `${rest},${last3}` : last3) + (frac !== undefined ? `.${frac}` : "");
}

export const AmountEntry = forwardRef<HTMLInputElement, { value: string; onChange: (v: string) => void; label?: string }>(
  function AmountEntry({ value, onChange, label = "Amount" }, outerRef) {
    const [focused, setFocused] = useState(false);
    const inner = useRef<HTMLInputElement | null>(null);
    const shake = useAnimationControls();
    const shown = value ? grouped(value) : "";
    const words = rupeesInWords(value);
    const size = shown.length <= 6 ? "text-[56px]" : shown.length <= 9 ? "text-[46px]" : "text-[40px]";

    const ref = useCallback((el: HTMLInputElement | null) => {
      inner.current = el;
      if (typeof outerRef === "function") outerRef(el);
      else if (outerRef) outerRef.current = el;
    }, [outerRef]);

    const toEnd = () => {
      const el = inner.current;
      if (el && (el.selectionStart !== el.value.length || el.selectionEnd !== el.value.length)) {
        el.setSelectionRange(el.value.length, el.value.length);
      }
    };

    function refuse() {
      haptic(18);
      shake.start({ x: [0, -8, 7, -4, 2, 0], transition: { duration: 0.3 } });
    }

    const caret = focused && <span aria-hidden className="amount-caret mx-[3px] w-[2px] self-stretch rounded-full bg-ink" />;

    return (
      <div className="relative mx-auto w-full max-w-[340px] text-center">
        <motion.div animate={shake} aria-hidden
          className={cn("flex h-[64px] items-center justify-center font-sans font-normal leading-none tracking-[-0.02em] text-ink num", size)}>
          <span className={cn("mr-1.5 transition-colors duration-base", value ? "text-ink" : "text-muted")}>₹</span>
          {shown ? (
            <>
              <AnimatePresence initial={false} mode="popLayout">
                {[...shown].map((ch, k) => (
                  <motion.span key={`${k}-${ch}`} layout="position" initial={{ y: 14, opacity: 0 }} animate={{ y: 0, opacity: 1 }}
                    exit={{ opacity: 0, transition: { duration: 0.08 } }} transition={{ type: "spring", stiffness: 700, damping: 38 }}>
                    {ch}
                  </motion.span>
                ))}
              </AnimatePresence>
              {caret}
            </>
          ) : (
            <>{caret}<span className="text-muted/60">0</span></>
          )}
        </motion.div>
        <p className="mt-2 min-h-[20px] text-[14px] text-ink-2" aria-live="polite">{words}</p>
        <input ref={ref} id="amount" name="amount" inputMode="decimal" autoComplete="off" aria-label={label}
          value={value}
          onFocus={() => { setFocused(true); requestAnimationFrame(toEnd); }} onBlur={() => setFocused(false)}
          onSelect={toEnd} onClick={toEnd} onMouseUp={toEnd}
          onKeyDown={(e) => {
            if (NAV_KEYS.has(e.key)) { e.preventDefault(); return; }
            if (e.key.length === 1 && !/[\d.]/.test(e.key) && !e.metaKey && !e.ctrlKey) { e.preventDefault(); refuse(); }
          }}
          onChange={(e) => { const v = clean(e.target.value); if (v === null) refuse(); else onChange(v); requestAnimationFrame(toEnd); }}
          className="absolute inset-x-0 top-0 h-[64px] w-full cursor-text rounded-chip bg-transparent text-transparent caret-transparent outline-none selection:bg-transparent focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent" />
      </div>
    );
  },
);
