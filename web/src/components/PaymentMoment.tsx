import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import { Timer } from "lucide-react";
import { haptic } from "@/lib/motion";
import type { Payment } from "@/lib/types";
import { cn, inr, initials, reducedMotion } from "@/lib/utils";

/**
 * The moment a payment happens, full screen.
 *
 * The green of the swipe fills the screen. The payee sits in the middle inside a ring of six
 * segments, one for each check the bank makes; they fill in turn while one calm line says
 * which check is running. When the ring closes it becomes a tick, the amount and the time
 * the bank took ("Completed in 0.42 s") rise into place, and the screen settles. A payment
 * that stops does so at the segment that stopped it, in amber or red, with one plain
 * sentence. Then it hands over to the receipt. A tap or Escape skips it.
 */
const CHECKS = ["Checking it isn't a repeat", "Checking how often you've paid", "Confirming who you're paying",
  "Checking your limits", "Scoring the risk", "Moving the money"];
const SEG = 0.2;                       // seconds per check
const R = 74, GAP_DEG = 9;

/** One sixth of the ring, as an SVG arc, starting at the top and going clockwise. */
function arc(k: number): string {
  const a0 = ((k * 60 + GAP_DEG / 2 - 90) * Math.PI) / 180, a1 = (((k + 1) * 60 - GAP_DEG / 2 - 90) * Math.PI) / 180;
  const p = (a: number) => `${(94 + R * Math.cos(a)).toFixed(2)} ${(94 + R * Math.sin(a)).toFixed(2)}`;
  return `M ${p(a0)} A ${R} ${R} 0 0 1 ${p(a1)}`;
}

type Outcome = { stop: number; tone: "success" | "warn" | "danger"; title: string; line: string };

function outcome(p: Payment): Outcome {
  const c = String(p.decline_code || "");
  const name = p.to_display_name || p.payee_ref || "your payee";
  if (p.status === "COMPLETED") return { stop: 6, tone: "success", title: `${inr(p.amount)} paid`, line: `to ${name}` };
  if (p.status === "CHALLENGE") return { stop: 4, tone: "warn", title: "Confirm it's you", line: "We sent a code to your email and phone." };
  if (p.status === "HELD") return { stop: 4, tone: "warn", title: "Held for a quick review", line: "Your money is safe. We'll finish or return it shortly." };
  const at = /RATE_LIMIT/.test(c) ? 1 : /LIMIT/.test(c) ? 3 : /RISK|BLOCK|FRAUD/.test(c) ? 4 : /INSUFFICIENT|FUNDS/.test(c) ? 5 : 2;
  return { stop: at, tone: "danger", title: "Payment declined", line: p.decline_message || "Nothing was taken from your account." };
}

const TONE = {
  success: { bg: "bg-pay", ink: "text-pay-ink" },
  warn: { bg: "bg-sun", ink: "text-sun-ink" },
  danger: { bg: "bg-danger", ink: "text-danger-ink" },
};

function seconds(p: Payment, clientMs: number): string {
  const ms = p.processing_ms ?? clientMs;
  return ms < 1000 ? `${(ms / 1000).toFixed(2)} s` : `${(ms / 1000).toFixed(1)} s`;
}

export function PaymentMoment({ p, clientMs, onDone }: { p: Payment; clientMs: number; onDone: () => void }) {
  const o = outcome(p);
  const still = reducedMotion();
  const [step, setStep] = useState(still ? o.stop : 0);   // segments filled so far
  const [settled, setSettled] = useState(still);
  const filled = Math.min(step, o.stop);
  const done = useRef(onDone);
  done.current = onDone;                // the parent re-renders; the timers must not restart

  useEffect(() => {
    if (!settled && step < o.stop) { const t = setTimeout(() => { setStep(step + 1); haptic(4); }, SEG * 1000); return () => clearTimeout(t); }
    if (!settled) {
      const t = setTimeout(() => { setSettled(true); haptic(o.tone === "success" ? [12, 40, 20] : 30); }, 160);
      return () => clearTimeout(t);
    }
    const t = setTimeout(() => done.current(), o.tone === "warn" && p.status === "CHALLENGE" ? 1500 : 2300);
    return () => clearTimeout(t);
  }, [step, settled, o.stop, o.tone, p.status]);

  useEffect(() => {
    const key = (e: KeyboardEvent) => { if (e.key === "Escape" || e.key === "Enter") done.current(); };
    addEventListener("keydown", key);
    return () => removeEventListener("keydown", key);
  }, []);

  const tone = settled ? TONE[o.tone] : TONE.success;

  return createPortal(
    <motion.div role="dialog" aria-modal aria-label={settled ? `${o.title} ${o.line}` : "Making your payment"}
      onClick={onDone}
      initial={still ? { opacity: 0 } : { clipPath: "circle(0% at 50% 88%)" }}
      animate={still ? { opacity: 1 } : { clipPath: "circle(150% at 50% 88%)" }}
      exit={{ opacity: 0, scale: 1.02, transition: { duration: 0.25 } }}
      transition={{ duration: 0.55, ease: [0.65, 0, 0.35, 1] }}
      className={cn("fixed inset-0 z-[60] flex cursor-pointer flex-col items-center justify-center px-6 transition-colors duration-500", tone.bg, tone.ink)}>

      <motion.p initial={{ opacity: 0, y: 10 }} animate={{ opacity: settled ? 0 : 1, y: 0 }} transition={{ delay: 0.25 }}
        className="mb-8 font-sans text-[34px] font-normal tracking-[-0.02em] num">{inr(p.amount)}</motion.p>

      {/* the ring of six checks around the payee */}
      <div className="relative size-[188px]">
        <svg viewBox="0 0 188 188" className="absolute inset-0" aria-hidden>
          {CHECKS.map((_, k) => (
            <g key={k}>
              <path d={arc(k)} fill="none" stroke="currentColor" strokeOpacity=".22" strokeWidth="7" strokeLinecap="round" />
              <motion.path d={arc(k)} fill="none" stroke="currentColor" strokeLinecap="round"
                initial={{ pathLength: 0 }}
                animate={{ pathLength: k < filled ? 1 : 0, strokeWidth: settled && o.tone !== "success" && k === filled - 1 ? 10 : 7 }}
                transition={{ duration: SEG * 0.85, ease: "easeOut" }} />
            </g>
          ))}
          {/* on success the gaps close: the six checks become one ring */}
          <motion.circle cx="94" cy="94" r={R} fill="none" stroke="currentColor" strokeWidth="9" strokeLinecap="round"
            style={{ rotate: -90, originX: "94px", originY: "94px" }}
            initial={{ pathLength: 0, opacity: 0 }}
            animate={settled && o.tone === "success" ? { pathLength: 1, opacity: 1 } : { pathLength: 0, opacity: 0 }}
            transition={{ duration: 0.35, ease: "easeInOut" }} />
        </svg>
        <div className="absolute inset-[24px] flex items-center justify-center rounded-full bg-white/15">
          <AnimatePresence mode="wait" initial={false}>
            {settled ? (
              <motion.svg key="mark" viewBox="0 0 48 48" className="size-16" initial={{ scale: 0.6, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
                transition={{ type: "spring", stiffness: 420, damping: 22 }} aria-hidden>
                <motion.path d={o.tone === "success" ? "M11 25 L20 34 L37 15" : o.tone === "warn" ? "M24 12 V27 M24 35 V35.5" : "M14 14 L34 34 M34 14 L14 34"}
                  fill="none" stroke="currentColor" strokeWidth="4.5" strokeLinecap="round" strokeLinejoin="round"
                  initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: 0.35, ease: "easeOut", delay: 0.05 }} />
              </motion.svg>
            ) : (
              <motion.span key="who" exit={{ scale: 0.7, opacity: 0 }} transition={{ duration: 0.15 }}
                className="font-sans text-[36px] font-semibold tracking-tight">{initials(p.to_display_name || "?")}</motion.span>
            )}
          </AnimatePresence>
        </div>
      </div>

      {/* one calm line while it runs; the outcome when it lands */}
      <div className="mt-9 min-h-[112px] w-full max-w-[360px] text-center">
        <AnimatePresence mode="wait">
          {settled ? (
            <motion.div key="end" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ type: "spring", stiffness: 300, damping: 26 }}>
              <p className="font-sans text-[32px] font-semibold tracking-[-0.02em] num">{o.title}</p>
              <p className="mt-1 text-[16px] opacity-85">{o.line}</p>
              <motion.p initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.25 }}
                className="mt-5 inline-flex items-center gap-1.5 rounded-full bg-current/12 px-3.5 py-1.5 text-[13px] font-semibold num">
                <Timer className="size-4" />
                {o.tone === "success" ? `Completed in ${seconds(p, clientMs)}` : `Checked in ${seconds(p, clientMs)}`}
                {o.tone === "success" ? ", six checks passed" : ""}
              </motion.p>
            </motion.div>
          ) : (
            <motion.p key={filled} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.12 }} className="text-[16px] font-medium" aria-live="polite">
              {CHECKS[Math.min(filled, 5)]}
            </motion.p>
          )}
        </AnimatePresence>
      </div>

      <motion.p initial={{ opacity: 0 }} animate={{ opacity: settled ? 0.7 : 0 }} transition={{ delay: 0.6 }}
        className="absolute bottom-[max(28px,env(safe-area-inset-bottom))] text-[13px] font-medium">Tap to see the receipt</motion.p>
    </motion.div>,
    document.body,
  );
}
