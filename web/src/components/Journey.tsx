import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { Check, Clock, X } from "lucide-react";
import { Avatar } from "./Payment";
import { haptic, spring } from "@/lib/motion";
import type { Payment } from "@/lib/types";
import { cn, inr, reducedMotion, short } from "@/lib/utils";

/**
 * The payment travelling through the bank's six checks, drawn from the real result:
 * a coin runs along the rail, each check lights up as it passes, and the panel shows what
 * that check looked at (the risk dial lands on the real score). Where the payment stopped,
 * the coin stops too. A completed payment ends with the panel turning green and the bolt
 * striking. About two seconds; a tap skips it; reduced motion skips it entirely.
 */
const NAMES = ["Duplicate", "Rate limit", "Payee", "Limits", "Risk", "Ledger"];
const STEP = 0.3;

function stopAt(p: Payment): { at: number; kind: "pass" | "fail" | "wait" } {
  const c = String(p.decline_code || "");
  if (p.status === "COMPLETED") return { at: 6, kind: "pass" };
  if (p.status === "CHALLENGE" || p.status === "HELD") return { at: 4, kind: "wait" };
  if (/RATE_LIMIT/.test(c)) return { at: 1, kind: "fail" };
  if (/LIMIT/.test(c)) return { at: 3, kind: "fail" };
  if (/RISK|BLOCK|FRAUD/.test(c)) return { at: 4, kind: "fail" };
  if (/INSUFFICIENT|FUNDS|LEDGER/.test(c)) return { at: 5, kind: "fail" };
  return { at: 2, kind: "fail" };
}

function Dial({ score, tone }: { score: number; tone: string }) {
  const angle = -90 + (Math.min(100, Math.max(0, score)) / 100) * 180;
  return (
    <div className="flex items-end gap-4">
      <svg viewBox="0 0 120 66" className="w-[124px]" aria-hidden>
        <path d="M10 60 A50 50 0 0 1 110 60" fill="none" stroke="currentColor" strokeOpacity=".18" strokeWidth="10" strokeLinecap="round" />
        <motion.path d="M10 60 A50 50 0 0 1 110 60" fill="none" className={tone} stroke="currentColor" strokeWidth="10" strokeLinecap="round"
          initial={{ pathLength: 0 }} animate={{ pathLength: Math.max(0.02, score / 100) }} transition={{ duration: 0.5, ease: [0.2, 0.9, 0.25, 1] }} />
        <motion.line x1="60" y1="60" x2="60" y2="18" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round"
          style={{ originX: "60px", originY: "60px" }} initial={{ rotate: -90 }} animate={{ rotate: [-90, angle + 12, angle] }} transition={{ duration: 0.55, times: [0, 0.7, 1] }} />
        <circle cx="60" cy="60" r="6" fill="currentColor" />
      </svg>
      <p className="pb-1 text-left text-[13px] leading-snug opacity-80"><b className="block font-display text-[26px] leading-none opacity-100 num">{score}</b>of 100</p>
    </div>
  );
}

function Detail({ i, p }: { i: number; p: Payment }) {
  const score = p.risk_score ?? 0;
  const tone = score >= 60 ? "text-danger" : score >= 30 ? "text-sun" : "text-success";
  switch (i) {
    case 0: return <p>Reference <b className="num">{short(p.txn_id)}</b>. Sending it twice can't move money twice.</p>;
    case 1: return (
      <div className="w-full max-w-[220px]">
        <div className="h-2 overflow-hidden rounded-full bg-current/15"><motion.div className="h-full rounded-full bg-success" initial={{ width: 0 }} animate={{ width: "18%" }} transition={{ duration: 0.25 }} /></div>
        <p className="mt-2">Well within the payments allowed a minute.</p>
      </div>
    );
    case 2: return (
      <div className="flex items-center gap-3 text-left">
        <Avatar name={p.to_display_name} className="size-10" />
        <p><b className="block">{p.to_display_name || p.payee_ref}</b>{p.own_transfer ? "Your own account" : p.payee_kind === "MOBILE" ? "Found by mobile number" : p.payee_kind === "PAY_ID" ? "Found by pay ID" : "Account checked"}</p>
      </div>
    );
    case 3: return <p>{p.own_transfer ? "Between your own accounts: no daily limit applies." : `${inr(p.amount)} is inside your per-payment and daily limits.`}</p>;
    case 4: return p.risk_score == null ? <p>Scored on device, payee, amount and recent activity.</p> : <Dial score={score} tone={tone} />;
    default: return (
      <div className="grid w-full max-w-[240px] gap-1.5 text-left num">
        <motion.p initial={{ x: -12, opacity: 0 }} animate={{ x: 0, opacity: 1 }} className="flex justify-between"><span>You</span><b>−{inr(p.amount)}</b></motion.p>
        <motion.p initial={{ x: 12, opacity: 0 }} animate={{ x: 0, opacity: 1 }} transition={{ delay: 0.08 }} className="flex justify-between"><span>{(p.to_display_name || "Payee").split(" ")[0]}</span><b>+{inr(p.amount)}</b></motion.p>
        <p className="mt-1 text-center text-xs opacity-70">Both sides in one transaction</p>
      </div>
    );
  }
}

export function Journey({ p, onDone }: { p: Payment; onDone: () => void }) {
  const { at, kind } = stopAt(p);
  const last = Math.min(at, 5);
  const [i, setI] = useState(0);
  const [finished, setFinished] = useState(false);
  const good = p.status === "COMPLETED";

  useEffect(() => {
    if (reducedMotion()) { onDone(); return; }
    if (i < last) { const t = setTimeout(() => { setI(i + 1); haptic(6); }, STEP * 1000); return () => clearTimeout(t); }
    const t1 = setTimeout(() => setFinished(true), 380);
    const t2 = setTimeout(onDone, good ? 1250 : 1000);
    return () => { clearTimeout(t1); clearTimeout(t2); };
  }, [i, last, good, onDone]);

  const nodeState = (n: number) => (n < i || (n === i && finished && kind === "pass") ? "pass" : n === i ? (finished ? kind : "live") : "todo");
  const flood = finished && good;

  return (
    <motion.button type="button" onClick={onDone} aria-label="Skip" initial={{ opacity: 0, scale: 0.97 }} animate={{ opacity: 1, scale: 1 }} transition={spring.page}
      className={cn("relative block w-full overflow-hidden rounded-card px-5 pt-6 pb-7 text-left transition-colors duration-500",
        flood ? "bg-success text-success-ink" : "on-dark bg-night text-on-night")}>
      <p className="text-[13px] font-semibold opacity-70" aria-live="polite">{finished ? (good ? "All six checks passed" : kind === "wait" ? "One more step" : "Stopped") : `Checking: ${NAMES[i]}`}</p>
      <p className="mt-1 font-display text-[30px] font-semibold leading-none tracking-[-0.03em] num">{inr(p.amount)}</p>

      {/* the rail */}
      <div className="relative mt-7 mb-2 h-10">
        <div className="absolute top-1/2 right-3 left-3 h-1 -translate-y-1/2 rounded-full bg-current/15" />
        <motion.div className="absolute top-1/2 left-3 h-1 -translate-y-1/2 rounded-full bg-success"
          animate={{ width: `calc(${(i / 5) * 100}% - ${(i / 5) * 24}px)` }} transition={{ duration: STEP * 0.9, ease: "easeOut" }} />
        <div className="absolute inset-x-0 top-0 flex justify-between">
          {NAMES.map((n, k) => {
            const s = nodeState(k);
            const Icon = s === "fail" ? X : s === "wait" ? Clock : Check;
            return (
              <div key={n} className="flex w-6 flex-col items-center">
                <motion.span animate={{ scale: s === "live" ? [1, 1.25, 1] : 1 }} transition={{ duration: 0.4, repeat: s === "live" ? Infinity : 0 }}
                  className={cn("mt-2 flex size-6 items-center justify-center rounded-full border-2 transition-colors",
                    s === "pass" ? "border-success bg-success text-success-ink" : s === "fail" ? "border-danger bg-danger text-danger-ink"
                      : s === "wait" ? "border-sun bg-sun text-sun-ink" : s === "live" ? "border-accent bg-night" : "border-current/25 bg-transparent")}>
                  {(s === "pass" || s === "fail" || s === "wait") && <Icon className="size-3.5" strokeWidth={3} />}
                </motion.span>
              </div>
            );
          })}
        </div>
        {/* the coin */}
        <motion.span aria-hidden className="absolute top-0 flex size-10 items-center justify-center rounded-full bg-accent font-bold text-accent-ink shadow-[0_0_0_4px_rgb(0_0_0/0.12),0_8px_24px_-6px_var(--color-accent)]"
          animate={{ left: `calc(${(i / 5) * 100}% - ${(i / 5) * 40}px)`, opacity: finished ? 0 : 1, scale: finished ? 0.5 : 1 }}
          transition={{ left: { type: "spring", stiffness: 260, damping: 24 }, opacity: { duration: 0.2 } }}>₹</motion.span>
      </div>
      <div className="flex justify-between text-[10px] font-semibold uppercase tracking-wide opacity-60">
        {NAMES.map((n) => <span key={n} className="w-12 text-center first:-ml-3 last:-mr-3">{n}</span>)}
      </div>

      {/* what this check looked at */}
      <motion.div key={finished ? "end" : i} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.18 }}
        className="mt-6 flex min-h-[78px] items-center justify-center text-center text-[14px] leading-snug">
        {finished && good ? <p className="font-display text-[22px] font-semibold">Paid</p> : <Detail i={i} p={p} />}
      </motion.div>

      {/* the bolt strikes once when the money moves */}
      {flood && (
        <svg viewBox="0 0 100 160" aria-hidden className="pointer-events-none absolute -top-4 right-6 h-[115%] text-sun">
          <motion.path d="M58 0 L22 86 L50 86 L34 160 L82 58 L54 58 L72 0 Z" fill="currentColor"
            initial={{ opacity: 0, scale: 0.6, y: -30 }} animate={{ opacity: [0, 1, 1, 0.9], scale: 1, y: 0 }} transition={{ duration: 0.35, ease: "easeOut" }} />
        </svg>
      )}
    </motion.button>
  );
}
