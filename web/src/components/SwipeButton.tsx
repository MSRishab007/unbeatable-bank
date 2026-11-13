import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { animate, motion, useMotionValue, useTransform } from "motion/react";
import { Check, ChevronsRight, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

const THUMB = 52;
const PAD = 6;

/**
 * Slide to pay. Dragging the thumb past 85% of the track confirms; letting go earlier springs
 * it back. The thumb is also a button: Enter or Space confirms, for keyboards and screen readers.
 */
export function SwipeButton({ label, onConfirm, disabled, done, demo, className }: {
  label: string;
  onConfirm?: () => Promise<unknown> | void;
  disabled?: boolean;
  /** Show the finished state (the parent decides when the payment is through). */
  done?: boolean;
  /** Plays a slide by itself, for the landing page. */
  demo?: boolean;
  className?: string;
}) {
  const track = useRef<HTMLDivElement>(null);
  const [max, setMax] = useState(240);
  const [busy, setBusy] = useState(false);
  const x = useMotionValue(0);
  const fill = useTransform(x, (v) => v + THUMB + PAD * 2);
  const textOpacity = useTransform(x, [0, max * 0.6], [1, 0]);

  useLayoutEffect(() => {
    const el = track.current;
    if (!el) return;
    const measure = () => setMax(Math.max(0, el.clientWidth - THUMB - PAD * 2));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (!done && !busy) animate(x, 0, { type: "spring", stiffness: 500, damping: 40 });
  }, [done, busy, x]);

  useEffect(() => {
    if (!demo) return;
    let stop = false;
    const loop = async () => {
      while (!stop) {
        await new Promise((r) => setTimeout(r, 1400));
        if (stop) break;
        await animate(x, max, { duration: 0.9, ease: [0.5, 0, 0.2, 1] });
        await new Promise((r) => setTimeout(r, 1300));
        await animate(x, 0, { duration: 0.35 });
      }
    };
    loop();
    return () => {
      stop = true;
    };
  }, [demo, max, x]);

  async function confirm() {
    if (disabled || busy || done) return;
    setBusy(true);
    await animate(x, max, { type: "spring", stiffness: 400, damping: 35 });
    try {
      await onConfirm?.();
    } finally {
      setBusy(false);
    }
  }

  const finished = done;
  return (
    <div
      ref={track}
      className={cn(
        "relative h-16 w-full select-none overflow-hidden rounded-full bg-go p-[6px] touch-pan-y",
        disabled && "opacity-50 saturate-50",
        className,
      )}
    >
      <motion.div style={{ width: fill }} className="absolute inset-y-0 left-0 rounded-full bg-go-deep" />
      <motion.span
        style={{ opacity: textOpacity }}
        className="pointer-events-none absolute inset-0 flex items-center justify-center pl-12 text-[15px] font-semibold text-white"
      >
        <span className="bg-[linear-gradient(90deg,rgba(255,255,255,.65)_0%,#fff_45%,rgba(255,255,255,.65)_60%)] bg-[length:200%_100%] bg-clip-text text-transparent animate-[shimmer_2.6s_linear_infinite]">
          {label}
        </span>
      </motion.span>
      {finished && (
        <span className="absolute inset-0 flex items-center justify-center text-[15px] font-semibold text-white">Done</span>
      )}
      <motion.button
        type="button"
        aria-label={`${label}. Press to confirm.`}
        disabled={disabled || busy || done}
        drag={disabled || busy || done || demo ? false : "x"}
        dragConstraints={{ left: 0, right: max }}
        dragElastic={0}
        dragMomentum={false}
        style={{ x, width: THUMB, height: THUMB }}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            confirm();
          }
        }}
        onDragEnd={() => {
          if (x.get() > max * 0.85) confirm();
          else animate(x, 0, { type: "spring", stiffness: 500, damping: 35 });
        }}
        className="relative z-10 flex cursor-grab items-center justify-center rounded-full bg-white text-go-deep shadow-[0_4px_14px_rgba(0,0,0,.18)] active:cursor-grabbing disabled:cursor-default"
      >
        {done ? <Check className="size-6" strokeWidth={3} /> : busy ? <Loader2 className="size-6 animate-spin" /> : <ChevronsRight className="size-6" strokeWidth={2.5} />}
      </motion.button>
    </div>
  );
}
