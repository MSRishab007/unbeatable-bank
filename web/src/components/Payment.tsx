import { useState } from "react";
import { motion } from "motion/react";
import { Check, X, Clock, Minus, ChevronRight, ShieldCheck, ShieldAlert } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "./ui/card";
import { Button } from "./ui/button";
import { Modal } from "./ui/dialog";
import { PinBoxes } from "./Prompts";
import { api } from "@/lib/api";
import { refreshMoney } from "@/lib/query";
import type { Payment } from "@/lib/types";
import { cn, groupDigits, initials, inr, short, when, words } from "@/lib/utils";

const AVATAR = ["#ffe7d6 #9a4a12", "#dff3e8 #0b7a48", "#e6e5ff #2d2ae6", "#fff1c2 #7a5a00", "#fde3ec #a2264f", "#dcf0fb #0e6390"];
export function Avatar({ name, className }: { name?: string | null; className?: string }) {
  const n = String(name || "?");
  const [bg, fg] = AVATAR[[...n].reduce((a, c) => a + c.charCodeAt(0), 0) % AVATAR.length]!.split(" ");
  return (
    <span className={cn("flex size-11 shrink-0 items-center justify-center rounded-full text-sm font-bold", className)} style={{ background: bg, color: fg }}>
      {initials(n)}
    </span>
  );
}

export const STATUS: Record<string, { label: string; tone: "good" | "bad" | "warn" | "neutral" }> = {
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
  return (
    <li>
      <button type="button" onClick={() => onOpen(p)} className="flex w-full items-center gap-3.5 px-4 py-3.5 text-left transition-colors hover:bg-mist sm:px-5">
        <Avatar name={name} />
        <span className="min-w-0 flex-1">
          <span className="block truncate font-semibold">{name}</span>
          <span className="block truncate text-[13px] text-muted">{when(p.created_at)}{p.note ? ` · ${p.note}` : ""}</span>
        </span>
        <span className="flex flex-col items-end gap-1">
          <span className={cn("font-semibold num", p.status === "DECLINED" || p.status === "CANCELLED" ? "text-muted line-through" : "text-ink")}>
            {p.status === "COMPLETED" ? "−" : ""}{inr(p.amount)}
          </span>
          {p.status !== "COMPLETED" && <StatusBadge status={p.status} />}
        </span>
        <ChevronRight className="size-4 text-muted/60" />
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

/** The six checks, in order, with where this payment stopped. Ticks in one after another. */
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

  return (
    <ol className="checks space-y-0">
      {names.map((name, i) => {
        const kind = i < stop ? "pass" : i === stop ? stopKind : p.status === "HELD" && i === 5 ? "wait" : "skip";
        const Icon = { pass: Check, fail: X, wait: Clock, skip: Minus }[kind];
        const tone = { pass: "bg-go text-white", fail: "bg-danger text-white", wait: "bg-volt text-ink", skip: "bg-line text-muted" }[kind];
        return (
          <motion.li
            key={name}
            className={cn("relative flex gap-3 pb-4 last:pb-0", kind)}
            initial={animate ? { opacity: 0, y: 6 } : false}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: animate ? 0.15 + i * 0.09 : 0 }}
          >
            {i < 5 && <span aria-hidden className={cn("absolute top-7 left-[11px] h-[calc(100%-1.5rem)] w-0.5", i < stop - 1 ? "bg-go/40" : "bg-line")} />}
            <span className={cn("relative z-10 flex size-6 shrink-0 items-center justify-center rounded-full", tone)}>
              <Icon className="size-3.5" strokeWidth={3} />
            </span>
            <div className="min-w-0 pt-0.5">
              <p className={cn("text-[15px] font-semibold", kind === "skip" && "text-muted")}>{name}</p>
              {(kind !== "skip" || i === 5) && (
                <p className="text-[13px] leading-snug text-muted">{i === stop && kind === "fail" ? p.decline_message || notes[i] : notes[i]}</p>
              )}
              {i === 4 && !!p.risk_reasons?.length && (
                <div className="mt-1.5 flex flex-wrap gap-1">{p.risk_reasons.map((r) => <Badge key={r} tone={kind === "pass" ? "neutral" : "warn"}>{words(r)}</Badge>)}</div>
              )}
            </div>
          </motion.li>
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

/** The whole story of one payment, and what the customer can do next. */
export function Slip({ p: initial, fresh = false, onChange }: { p: Payment; fresh?: boolean; onChange?: (p: Payment) => void }) {
  const [p, setP] = useState(initial);
  const [code, setCode] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState(false);
  const update = (next: Payment) => { setP(next); onChange?.(next); refreshMoney(); };
  const s = STATUS[p.status] || { label: words(p.status), tone: "neutral" as const };
  const good = p.status === "COMPLETED";
  const bad = p.status === "DECLINED" || p.status === "CANCELLED";

  async function confirm(value = code) {
    if (busy || value.length !== 6) return;
    setBusy(true); setErr("");
    try {
      const r = await api<{ status: number; data: any }>("POST", `/api/payments/${p.txn_id}/confirm`, { otp: value },
        { full: true, accept: [400, 401, 403, 409, 422] });
      if (r.data?.txn_id && r.data.status !== "CHALLENGE") update(r.data);
      else { setErr(r.data?.message || r.data?.decline_message || "That code isn't right. Check your inbox and try again."); setCode(""); }
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }

  return (
    <div className="slip" data-status={p.status}>
      <div className="flex flex-col items-center text-center">
        <motion.span
          key={p.status}
          initial={fresh ? { scale: 0.3, rotate: -30, opacity: 0 } : false}
          animate={{ scale: 1, rotate: 0, opacity: 1 }}
          transition={{ type: "spring", stiffness: 260, damping: 16 }}
          className={cn("flex size-16 items-center justify-center rounded-full",
            good ? "bg-go text-white" : bad ? "bg-danger text-white" : "bg-volt text-ink")}
        >
          {good ? <Check className="size-8" strokeWidth={3} /> : bad ? <X className="size-8" strokeWidth={3} /> : p.status === "HELD" ? <ShieldAlert className="size-8" /> : <ShieldCheck className="size-8" />}
        </motion.span>
        <p className="mt-4 font-display text-[40px] font-semibold tracking-tight num">{inr(p.amount)}</p>
        <p className="text-[15px] text-muted">
          to <b className="font-semibold text-ink">{p.to_display_name || p.payee_ref}</b>
          {p.to_account_number ? ` · ${groupDigits(p.to_account_number)}` : ""}
        </p>
        <Badge tone={s.tone} className="mt-3 stamp">{s.label}</Badge>
        <p className="message mt-4 max-w-sm text-[15px] leading-relaxed text-ink-2">{HEADLINE[p.status]?.(p) || p.message || ""}</p>
      </div>

      {p.status === "CHALLENGE" && (
        <form className="mt-6 space-y-4 rounded-3xl bg-mist p-5" onSubmit={(e) => { e.preventDefault(); confirm(); }}>
          <p className="text-center text-sm font-semibold">Enter the 6-digit code</p>
          <PinBoxes kind="OTP" mask={false} value={code} onChange={setCode} onComplete={(v) => confirm(v)} invalid={!!err} />
          {err && <p role="alert" className="text-center text-sm text-danger">{err}</p>}
          <Button type="submit" variant="go" size="lg" className="w-full" disabled={busy || code.length !== 6}>Confirm payment</Button>
          <p className="text-center text-[13px] text-muted">The code is in your <a href="#/inbox" className="font-semibold text-cobalt">inbox</a> and works for five minutes.</p>
        </form>
      )}
      {p.status === "HELD" && (
        <div className="mt-6 flex flex-col items-center gap-2">
          <Button variant="danger-ghost" className="border border-danger/30" disabled={busy} onClick={async () => {
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

      <div className="mt-7 rounded-3xl border border-line p-5">
        <Checks p={p} animate={fresh} />
      </div>

      <div className="mt-5 flex flex-wrap items-center justify-between gap-2 text-[13px] text-muted">
        <span>Ref {short(p.txn_id)} · {when(p.created_at)}{p.channel === "QR_PAYMENT" ? " · by QR" : ""}</span>
        {good && <button type="button" className="font-semibold text-cobalt" onClick={() => setReport(true)}>Something wrong?</button>}
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
        <textarea required minLength={5} maxLength={1000} value={text} onChange={(e) => setText(e.target.value)} rows={4}
          className="w-full rounded-2xl border border-line p-4 text-[15px] outline-none focus:border-cobalt focus:ring-4 focus:ring-cobalt/12" placeholder="What happened?" />
        <Button type="submit" variant="danger" size="lg" className="w-full" disabled={busy}>Send report</Button>
      </form>
    </Modal>
  );
}

/** Any payment, opened from a list. */
export function PaymentSheet({ p, onClose }: { p: Payment | null; onClose: () => void }) {
  return (
    <Modal open={!!p} onOpenChange={(o) => !o && onClose()} title="Payment" className="sm:w-[min(92vw,520px)]">
      {p && <Slip p={p} />}
    </Modal>
  );
}
