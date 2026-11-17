import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { animate, motion, useMotionValue, useMotionValueEvent, useTransform } from "motion/react";
import { Check, ChevronRight, ChevronsRight, Loader2 } from "lucide-react";
import { haptic, spring } from "@/lib/motion";
import { cn } from "@/lib/utils";

const THUMB = 52;
const PAD = 6;
const COMMIT = 0.85;

/**
 * Swipe to pay. The thumb drags along the track and the track fills behind it. Past 85% it
 * "arms" (a tick you can feel on a phone, and the thumb swells); letting go there pays,
 * letting go earlier springs it back. The thumb is also a button, so Enter or Space pays
 * for keyboards and screen readers.
 */
export function SwipeButton({ label, onConfirm, disabled, done, demo, className }: {
  label: string;
  onConfirm?: () => Promise<unknown> | void;
  disabled?: boolean;
  /** Show the finished state (the parent decides when the payment is through). */
  done?: boolean;
  /** Plays a swipe by itself, for the landing page. */
  demo?: boolean;
  className?: string;
}) {
  const track = useRef<HTMLDivElement>(null);
  const [max, setMax] = useState(240);
  const [busy, setBusy] = useState(false);
  const [armed, setArmed] = useState(false);
  const [demoDone, setDemoDone] = useState(false);
  const x = useMotionValue(0);
  const fill = useTransform(x, (v) => Math.max(0, v) + THUMB + PAD * 2);
  const textOpacity = useTransform(x, [0, max * 0.55], [1, 0]);
  const textShift = useTransform(x, [0, max], [0, 24]);
  const finished = done || demoDone;

  useLayoutEffect(() => {
    const el = track.current;
    if (!el) return;
    const measure = () => setMax(Math.max(0, el.clientWidth - THUMB - PAD * 2));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // The tick as the thumb crosses the line, in either direction.
  useMotionValueEvent(x, "change", (v) => {
    const now = max > 0 && v >= max * COMMIT;
    if (now !== armed) {
      setArmed(now);
      if (now && !demo) haptic(8);
    }
  });

  useEffect(() => {
    if (!done && !busy && !demo) animate(x, 0, spring.bouncy);
  }, [done, busy, demo, x]);

  useEffect(() => {
    if (!demo) return;
    let stop = false;
    const pause = (ms: number) => new Promise((r) => setTimeout(r, ms));
    (async () => {
      while (!stop) {
        await pause(1200);
        if (stop) break;
        await animate(x, max, { duration: 0.85, ease: [0.5, 0, 0.2, 1] });
        setDemoDone(true);
        await pause(1900);
        setDemoDone(false);
        await animate(x, 0, spring.snappy);
        await pause(1400);
      }
    })();
    return () => {
      stop = true;
    };
  }, [demo, max, x]);

  async function confirm() {
    if (disabled || busy || done) return;
    setBusy(true);
    haptic([12, 40, 18]);
    await animate(x, max, spring.snappy);
    try {
      await onConfirm?.();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      ref={track}
      data-armed={armed || undefined}
      className={cn(
        "relative h-16 w-full touch-pan-y select-none overflow-hidden rounded-full p-[6px] transition-colors duration-base",
        disabled ? "bg-sunken text-muted" : "bg-pay text-pay-ink",
        className,
      )}
    >
      {/* The one gradient in the app: it shows how far the thumb has travelled. */}
      {!disabled && <motion.div style={{ width: fill }} className="absolute inset-y-0 left-0 rounded-full bg-linear-to-r from-pay-hover from-60% to-pay" />}
      <motion.span
        style={{ opacity: finished ? 0 : textOpacity, x: textShift }}
        className="pointer-events-none absolute inset-0 flex items-center justify-center gap-1 pr-3 pl-16 text-[15px] font-semibold"
      >
        <span className="truncate">{label}</span>
        {!disabled && (
          <span aria-hidden className="flex">
            {[0, 1, 2].map((i) => (
              <ChevronRight key={i} className="-mx-1 size-4 animate-nudge" style={{ animationDelay: `${i * 0.16}s` }} strokeWidth={3} />
            ))}
          </span>
        )}
      </motion.span>
      {finished && (
        <motion.span initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="absolute inset-0 flex items-center justify-center text-[15px] font-semibold text-pay-ink">
          {demo ? "Paid" : "Done"}
        </motion.span>
      )}
      <motion.button
        type="button"
        aria-label={`${label}. Press Enter to confirm.`}
        disabled={disabled || busy || done}
        tabIndex={demo ? -1 : undefined}
        aria-hidden={demo || undefined}
        drag={disabled || busy || done || demo ? false : "x"}
        dragConstraints={{ left: 0, right: max }}
        dragElastic={0.04}
        dragMomentum={false}
        style={{ x, width: THUMB, height: THUMB }}
        animate={{ scale: armed && !finished ? 1.1 : 1 }}
        whileTap={disabled || demo ? undefined : { scale: 1.06 }}
        transition={spring.bouncy}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            confirm();
          }
        }}
        onDragEnd={() => {
          if (x.get() >= max * COMMIT) confirm();
          else animate(x, 0, spring.bouncy);
        }}
        className={cn("relative z-10 flex cursor-grab items-center justify-center rounded-full bg-surface active:cursor-grabbing disabled:cursor-default", disabled ? "text-muted" : "text-pay-hover shadow-thumb")}
      >
        {finished ? <Check className="size-6" strokeWidth={3} /> : busy ? <Loader2 className="size-6 animate-spin" /> : <ChevronsRight className="size-6" strokeWidth={2.5} />}
      </motion.button>
    </div>
  );
}
