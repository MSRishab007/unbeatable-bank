import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { Check, ChevronRight, Clock, Hourglass, KeyRound, Minus, X } from "lucide-react";
import { toast } from "sonner";
import { Badge, type Tone } from "./ui/card";
import { Button } from "./ui/button";
import { CountUp } from "./ui/count-up";
import { Modal } from "./ui/dialog";
import { Textarea } from "./ui/input";
import { PinBoxes } from "./Prompts";
import { api } from "@/lib/api";
import { haptic, spring } from "@/lib/motion";
import { refreshMoney } from "@/lib/query";
import type { Payment } from "@/lib/types";
import { cn, groupDigits, initials, inr, reducedMotion, short, when, words } from "@/lib/utils";

const AVATAR = ["bg-sun text-sun-ink", "bg-accent-soft text-accent-text", "bg-night text-on-night", "bg-success-soft text-success-text", "bg-sunken text-ink", "bg-danger-soft text-danger"];
export function Avatar({ name, className }: { name?: string | null; className?: string }) {
  const n = String(name || "?");
  const tone = AVATAR[[...n].reduce((a, c) => a + c.charCodeAt(0), 0) % AVATAR.length];
  return <span className={cn("flex size-11 shrink-0 items-center justify-center rounded-full text-sm font-bold", tone, className)}>{initials(n)}</span>;
}

export const STATUS: Record<string, { label: string; tone: Tone }> = {
  COMPLETED: { label: "Paid", tone: "good" },
  DECLINED: { label: "Declined", tone: "bad" },
  CHALLENGE: { label: "Confirm it's you", tone: "warn" },
  HELD: { label: "Under review", tone: "warn" },
  PROCESSING: { label: "Processing", tone: "warn" },
  CANCELLED: { label: "Cancelled", tone: "bad" },
};
export const StatusBadge = ({ status }: { status: string }) => {
  const s = STATUS[status] || { label: words(status), tone: "neutral" as const };
  return <Badge tone={s.tone}>{s.label}</Badge>;
};

export function PaymentRow({ p, onOpen }: { p: Payment; onOpen: (p: Payment) => void }) {
  const name = p.to_display_name || p.payee_ref || "Payment";
  const void_ = p.status === "DECLINED" || p.status === "CANCELLED";
  return (
    <li>
      <button type="button" onClick={() => onOpen(p)} className="press-row flex w-full items-center gap-3.5 px-4 py-3.5 text-left hover:bg-bg sm:px-5">
        <Avatar name={name} />
        <span className="min-w-0 flex-1">
          <span className="block truncate font-semibold">{name}</span>
          <span className="block truncate text-[13px] text-muted">{when(p.created_at)}{p.note ? `, ${p.note}` : ""}</span>
        </span>
        <span className="flex flex-col items-end gap-1">
          <span className={cn("font-semibold num", void_ ? "text-muted line-through" : "text-ink")}>
            {p.status === "COMPLETED" ? "−" : ""}{inr(p.amount)}
          </span>
          {p.status !== "COMPLETED" && <StatusBadge status={p.status} />}
        </span>
        <ChevronRight className="size-4 shrink-0 text-muted" />
      </button>
    </li>
  );
}

function failedStage(code?: string | null) {
  const c = String(code || "");
  if (/RATE_LIMIT|TOO_MANY/.test(c)) return 1;
  if (/LIMIT/.test(c)) return 3;
  if (/RISK|BLOCK|FRAUD|OTP|CHALLENGE|DENIED|REVIEW/.test(c)) return 4;
  if (/INSUFFICIENT|FUNDS|LEDGER/.test(c)) return 5;
  return 2;
}

/** The six checks, in order, with where this payment stopped. They tick in one after another. */
export function Checks({ p, animate = true }: { p: Payment; animate?: boolean }) {
  const names = ["Duplicate check", "Rate limits", "Account and payee", "Your limits", "Risk engine", "Ledger"];
  const notes = [
    p.replayed ? "We'd already seen this payment reference, so this is the original result, not a second payment."
      : "One reference: sending it twice can't move money twice.",
    "Within the allowed number of payments a minute.",
    p.to_display_name ? `Paying ${p.to_display_name}.` : "Your account can pay and the payee can receive.",
    "Within your per-payment, per-day and new-payee limits.",
    p.risk_score == null ? "Scored on device, payee, amount and recent activity." : `Risk score ${p.risk_score} of 100: ${words(p.risk_decision)}.`,
    "Debited and credited in one transaction.",
  ];
  let stop = 6;
  let stopKind: "fail" | "wait" = "fail";
  if (p.status === "DECLINED") stop = failedStage(p.decline_code);
  else if (p.status === "CHALLENGE" || p.status === "HELD") { stop = 4; stopKind = "wait"; }
  else if (p.status === "PROCESSING") { stop = 5; stopKind = "wait"; }
  if (stop === 4 && p.status === "DECLINED") notes[4] = p.risk_score == null ? "Stopped by the risk engine." : `Risk score ${p.risk_score} of 100: stopped.`;
  if (p.status === "HELD") notes[5] = "The money is set aside in your account until an analyst decides.";
  if (p.status === "DECLINED") notes[5] = "No money moved.";
  const step = 0.11;

  return (
    <ol className="checks">
      {names.map((name, i) => {
        const kind = i < stop ? "pass" : i === stop ? stopKind : p.status === "HELD" && i === 5 ? "wait" : "skip";
        const Icon = { pass: Check, fail: X, wait: Clock, skip: Minus }[kind];
        const tone = { pass: "bg-success text-success-ink", fail: "bg-danger text-danger-ink", wait: "bg-sun text-sun-ink", skip: "bg-sunken text-muted" }[kind];
        const at = animate ? 0.25 + i * step : 0;
        return (
          <li key={name} className={cn("relative flex gap-3 pb-4 last:pb-0", kind)}>
            {i < 5 && (
              <span aria-hidden className="absolute top-7 left-[11px] h-[calc(100%-1.75rem)] w-0.5 overflow-hidden rounded-full bg-line">
                {i < stop - 1 && <motion.span className="block size-full origin-top bg-success" initial={animate ? { scaleY: 0 } : false} animate={{ scaleY: 1 }} transition={{ delay: at + 0.06, duration: step }} />}
              </span>
            )}
            <motion.span className={cn("relative z-10 flex size-6 shrink-0 items-center justify-center rounded-full", tone)}
              initial={animate ? { scale: 0 } : false} animate={{ scale: 1 }} transition={{ ...spring.bouncy, delay: at }}>
              <Icon className="size-3.5" strokeWidth={3} />
            </motion.span>
            <motion.div className="min-w-0 pt-0.5" initial={animate ? { opacity: 0, x: -6 } : false} animate={{ opacity: 1, x: 0 }} transition={{ delay: at, duration: 0.2 }}>
              <p className={cn("text-[15px] font-semibold", kind === "skip" && "text-muted")}>{name}</p>
              {(kind !== "skip" || i === 5) && (
                <p className="text-[13px] leading-snug text-muted">{i === stop && kind === "fail" ? p.decline_message || notes[i] : notes[i]}</p>
              )}
              {i === 4 && !!p.risk_reasons?.length && (
                <div className="mt-1.5 flex flex-wrap gap-1">{p.risk_reasons.map((r) => <Badge key={r} tone={kind === "pass" ? "neutral" : "warn"}>{words(r)}</Badge>)}</div>
              )}
            </motion.div>
          </li>
        );
      })}
    </ol>
  );
}

const HEADLINE: Record<string, (p: Payment) => string> = {
  COMPLETED: (p) => `Sent to ${p.to_display_name || p.payee_ref}.`,
  DECLINED: (p) => p.decline_message || "This payment was declined. No money moved.",
  CHALLENGE: () => "This looks different from your usual payments, so we sent a code to your inbox and email.",
  HELD: () => "We've paused this payment for a quick review. The money is set aside and hasn't left your account.",
};

/** What the top of the slip looks like in each state: its own surface, mark and movement. */
const LOOK: Record<string, { band: string; disc: string }> = {
  COMPLETED: { band: "bg-success text-success-ink", disc: "bg-surface text-success-text" },
  CHALLENGE: { band: "bg-sun text-sun-ink", disc: "bg-night text-sun" },
  HELD: { band: "on-dark bg-night text-on-night", disc: "bg-sun text-sun-ink" },
  DECLINED: { band: "bg-danger-soft text-ink", disc: "bg-danger text-danger-ink" },
};
LOOK.CANCELLED = LOOK.DECLINED!;
LOOK.PROCESSING = LOOK.CHALLENGE!;

const SPARKS = Array.from({ length: 12 }, (_, i) => {
  const angle = (i / 12) * Math.PI * 2 + (i % 2 ? 0.2 : -0.1);
  const far = 62 + (i % 3) * 16;
  return { x: Math.cos(angle) * far, y: Math.sin(angle) * far, tone: ["bg-sun", "bg-surface", "bg-accent"][i % 3]!, long: i % 2 === 0, turn: (angle * 180) / Math.PI };
});

/** The moment a payment goes through: the tick draws itself, two rings go out, and a burst of flat sparks flies. */
function StatusMark({ status, fresh }: { status: string; fresh: boolean }) {
  const look = LOOK[status] || LOOK.CHALLENGE!;
  const good = status === "COMPLETED";
  const bad = status === "DECLINED" || status === "CANCELLED";
  const play = fresh && !reducedMotion();
  return (
    <span className="relative flex size-[72px] items-center justify-center">
      {good && play && [0, 0.14].map((d) => (
        <motion.span key={d} aria-hidden className="absolute inset-0 rounded-full border-2 border-current" initial={{ scale: 0.8, opacity: 0.8 }} animate={{ scale: 2.5, opacity: 0 }} transition={{ delay: 0.12 + d, duration: 0.8, ease: "easeOut" }} />
      ))}
      {good && play && SPARKS.map((s, i) => (
        <motion.span key={i} aria-hidden className={cn("absolute rounded-full", s.tone, s.long ? "h-1.5 w-3.5" : "size-2")} style={{ rotate: s.turn }}
          initial={{ x: 0, y: 0, opacity: 1, scale: 0.4 }} animate={{ x: s.x, y: s.y, opacity: [1, 1, 0], scale: 1 }} transition={{ delay: 0.16, duration: 0.75, ease: [0.1, 0.8, 0.2, 1] }} />
      ))}
      <motion.span
        key={status}
        initial={play ? { scale: 0.2, rotate: good ? -40 : 0 } : false}
        animate={{ scale: 1, rotate: 0, x: bad && play ? [0, -9, 9, -6, 6, 0] : 0 }}
        transition={{ ...spring.bouncy, x: { delay: 0.25, duration: 0.36 } }}
        className={cn("relative flex size-[72px] items-center justify-center rounded-full", look.disc)}
      >
        {good ? (
          <svg viewBox="0 0 24 24" className="size-9" fill="none" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <motion.path d="M5 12.5l4.6 4.6L19 7.5" initial={play ? { pathLength: 0 } : false} animate={{ pathLength: 1 }} transition={{ delay: 0.18, duration: 0.32, ease: "easeOut" }} />
          </svg>
        ) : bad ? <X className="size-9" strokeWidth={3} /> : status === "HELD" ? <Hourglass className="size-8" /> : <KeyRound className="size-8" />}
      </motion.span>
    </span>
  );
}

/** The whole story of one payment, and what the customer can do next. */
export function Slip({ p: initial, fresh = false, onChange }: { p: Payment; fresh?: boolean; onChange?: (p: Payment) => void }) {
  const [p, setP] = useState(initial);
  const [code, setCode] = useState("");
  const [err, setErr] = useState("");
  const [shake, setShake] = useState(0);
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState(false);
  const [changed, setChanged] = useState(false);
  const update = (next: Payment) => { setP(next); setChanged(true); onChange?.(next); refreshMoney(); };
  const s = STATUS[p.status] || { label: words(p.status), tone: "neutral" as const };
  const look = LOOK[p.status] || LOOK.CHALLENGE!;
  const good = p.status === "COMPLETED";
  const lively = fresh || changed;

  useEffect(() => {
    if (!lively) return;
    if (good) haptic([14, 50, 22]);
    else if (p.status === "DECLINED") haptic([30, 40, 30]);
  }, [lively, good, p.status]);

  async function confirm(value = code) {
    if (busy || value.length !== 6) return;
    setBusy(true); setErr("");
    try {
      const r = await api<{ status: number; data: any }>("POST", `/api/payments/${p.txn_id}/confirm`, { otp: value },
        { full: true, accept: [400, 401, 403, 409, 422] });
      if (r.data?.txn_id && r.data.status !== "CHALLENGE") update(r.data);
      else { setErr(r.data?.message || r.data?.decline_message || "That code isn't right. Check your inbox and try again."); setCode(""); setShake((n) => n + 1); }
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }

  return (
    <div className="slip" data-status={p.status}>
      <motion.div key={p.status} initial={lively ? { opacity: 0.6, scale: 0.96 } : false} animate={{ opacity: 1, scale: 1 }} transition={spring.page}
        className={cn("flex flex-col items-center overflow-hidden rounded-card px-5 pt-8 pb-7 text-center", look.band)}>
        <StatusMark status={p.status} fresh={lively} />
        <p className="mt-5 font-display text-[38px] font-semibold leading-none tracking-[-0.04em] num">
          {lively && good ? <CountUp paise={p.amount} /> : inr(p.amount)}
        </p>
        <p className="mt-2 text-[15px]">
          to <b className="font-semibold">{p.to_display_name || p.payee_ref}</b>
          {p.to_account_number ? <span className="num">, {groupDigits(p.to_account_number)}</span> : ""}
        </p>
        <span className="stamp mt-4 rounded-full bg-surface px-3 py-1 text-xs font-bold text-ink">{s.label}</span>
      </motion.div>
      <p className="message mx-auto mt-4 max-w-sm text-center text-[15px] leading-relaxed text-ink-2">{HEADLINE[p.status]?.(p) || p.message || ""}</p>

      {p.status === "CHALLENGE" && (
        <form className="mt-5 space-y-4" onSubmit={(e) => { e.preventDefault(); confirm(); }}>
          <p className="text-center text-sm font-semibold">Enter the 6-digit code</p>
          <PinBoxes kind="OTP" name="otp" mask={false} value={code} onChange={setCode} onComplete={(v) => confirm(v)} invalid={!!err && !code} shake={shake} />
          {err && <p role="alert" className="text-center text-sm font-medium text-danger">{err}</p>}
          <Button type="submit" variant="pay" size="lg" className="w-full" disabled={busy || code.length !== 6}>Confirm payment</Button>
          <p className="text-center text-[13px] text-muted">The code is in your <a href="#/inbox" className="font-semibold text-accent-text underline underline-offset-2">inbox</a> and works for five minutes.</p>
        </form>
      )}
      {p.status === "HELD" && (
        <div className="mt-5 flex flex-col items-center gap-2">
          <Button variant="danger-ghost" className="border border-danger/40" disabled={busy} onClick={async () => {
            setBusy(true);
            try {
              const r = await api("POST", `/api/payments/${p.txn_id}/deny`, undefined, { accept: [422] });
              toast.success("Payment cancelled. We've protected your account.");
              update(r?.txn_id ? r : { ...p, status: "DECLINED", decline_message: "You told us this wasn't you. The payment was cancelled." });
            } catch (e) { toast.error((e as Error).message); } finally { setBusy(false); }
          }}>This wasn't me: cancel it</Button>
          <span className="text-[13px] text-muted">If you made this payment, there's nothing to do.</span>
        </div>
      )}

      <div className="mt-6 rounded-card bg-bg p-5">
        <Checks key={p.status} p={p} animate={lively} />
      </div>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-2 text-[13px] text-muted">
        <span>Ref {short(p.txn_id)}, {when(p.created_at)}{p.channel === "QR_PAYMENT" ? ", paid by QR" : ""}</span>
        {good && <button type="button" className="press rounded-full font-semibold text-accent-text underline-offset-2 hover:underline" onClick={() => setReport(true)}>Report a problem</button>}
      </div>
      <ReportPayment p={p} open={report} onOpenChange={setReport} />
    </div>
  );
}

function ReportPayment({ p, open, onOpenChange }: { p: Payment; open: boolean; onOpenChange: (o: boolean) => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Report this payment" description="Tell us what's wrong. A fraud analyst will open a case and contact you.">
      <form className="space-y-4" onSubmit={async (e) => {
        e.preventDefault(); setBusy(true);
        try {
          const c = await api("POST", "/api/reports", { txn_id: p.txn_id, description: text });
          toast.success(`Report received: ${c.case_number}`); onOpenChange(false); setText("");
        } catch (ex) { toast.error((ex as Error).message); } finally { setBusy(false); }
      }}>
        <Textarea required minLength={5} maxLength={1000} value={text} onChange={(e) => setText(e.target.value)} rows={4} placeholder="What happened?" aria-label="What happened?" />
        <Button type="submit" variant="danger" size="lg" className="w-full" disabled={busy}>Send report</Button>
      </form>
    </Modal>
  );
}

/** Any payment, opened from a list. */
export function PaymentSheet({ p, onClose }: { p: Payment | null; onClose: () => void }) {
  return (
    <Modal open={!!p} onOpenChange={(o) => !o && onClose()} title="Payment" className="sm:w-[min(92vw,500px)]">
      {p && <Slip p={p} />}
    </Modal>
  );
}
