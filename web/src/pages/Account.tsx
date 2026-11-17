import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { ArrowDownLeft, ArrowLeft, ArrowUpRight, AtSign, Copy, ExternalLink, Gauge, QrCode, Send, Snowflake } from "lucide-react";
import { toast } from "sonner";
import { accountLayoutId, accountTone, StatusPill } from "@/components/AccountCard";
import { Empty, Section, sectionLink } from "@/components/AppShell";
import { Mark } from "@/components/Logo";
import { Card, Notice, Skeleton } from "@/components/ui/card";
import { CountUp } from "@/components/ui/count-up";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/input";
import { api, get } from "@/lib/api";
import { spring } from "@/lib/motion";
import { queryClient } from "@/lib/query";
import { withPinContext } from "@/lib/session";
import { payable, PRODUCT, type Account, type Entry } from "@/lib/types";
import { cn, copyText, day, groupDigits, inr, toPaise, when, words } from "@/lib/utils";

type Balance = { available_balance: number; ledger_balance: number; accrued_interest?: number };

function Limits({ a, open, onOpenChange }: { a: Account; open: boolean; onOpenChange: (o: boolean) => void }) {
  const [per, setPer] = useState(String(a.per_txn_limit / 100));
  const [daily, setDaily] = useState(String(a.daily_limit / 100));
  const [busy, setBusy] = useState(false);
  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Payment limits"
      description="Lower limits apply at once. Higher ones start 24 hours later, so nobody can raise them and empty the account in one go.">
      <form className="space-y-5" onSubmit={async (e) => {
        e.preventDefault();
        const p = toPaise(per), d = toPaise(daily);
        if (!p || !d) return toast.error("Enter both limits in rupees.");
        setBusy(true);
        try {
          const r = await withPinContext("Changing your payment limits", () =>
            api("PATCH", `/api/accounts/${a.account_id}/limits`, { per_txn_limit: p, daily_limit: d }));
          toast.success(r.pending_limits_at ? `Saved. The higher limit starts ${when(r.pending_limits_at)}.` : "Limits saved");
          queryClient.invalidateQueries({ queryKey: ["account", a.account_id] });
          onOpenChange(false);
        } catch (ex) { toast.error((ex as Error).message); } finally { setBusy(false); }
      }}>
        <div className="grid grid-cols-2 gap-4">
          <Field label="Per payment (₹)" htmlFor="lim-per"><Input id="lim-per" inputMode="decimal" value={per} onChange={(e) => setPer(e.target.value)} /></Field>
          <Field label="Per day (₹)" htmlFor="lim-day"><Input id="lim-day" inputMode="decimal" value={daily} onChange={(e) => setDaily(e.target.value)} /></Field>
        </div>
        <Button type="submit" size="lg" className="w-full" disabled={busy}>Save limits</Button>
      </form>
    </Modal>
  );
}

function PayId({ a, open, onOpenChange }: { a: Account; open: boolean; onOpenChange: (o: boolean) => void }) {
  const [alias, setAlias] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Choose a pay ID" description="People can pay you with this instead of your account number.">
      <form className="space-y-5" onSubmit={async (e) => {
        e.preventDefault(); setBusy(true);
        try {
          const r = await api("POST", `/api/accounts/${a.account_id}/alias`, { alias: alias.trim() });
          toast.success(`Your pay ID is ${r.alias}`); copyText(r.alias); onOpenChange(false);
        } catch (ex) { toast.error((ex as Error).message); } finally { setBusy(false); }
      }}>
        <Field label="Pay ID" hint="Letters, numbers, dots and dashes." htmlFor="alias">
          <div className="relative">
            <Input id="alias" required minLength={3} maxLength={30} pattern="[A-Za-z0-9._\-]+" value={alias} onChange={(e) => setAlias(e.target.value)} placeholder="yourname" className="pr-32" autoComplete="off" />
            <span className="pointer-events-none absolute top-1/2 right-4 -translate-y-1/2 text-[15px] text-muted">@unbeatable</span>
          </div>
        </Field>
        <Button type="submit" size="lg" className="w-full" disabled={busy}>Save pay ID</Button>
      </form>
    </Modal>
  );
}

function Freeze({ a, open, onOpenChange }: { a: Account; open: boolean; onOpenChange: (o: boolean) => void }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Freeze this account"
      description="No money can leave while it's frozen. Unfreezing needs two bank officers, so only do this if something is wrong.">
      <form className="space-y-5" onSubmit={async (e) => {
        e.preventDefault(); setBusy(true);
        try {
          await api("POST", `/api/accounts/${a.account_id}/freeze`, { reason });
          toast.success("Account frozen");
          queryClient.invalidateQueries({ queryKey: ["account", a.account_id] });
          queryClient.invalidateQueries({ queryKey: ["accounts"] });
          onOpenChange(false);
        } catch (ex) { toast.error((ex as Error).message); } finally { setBusy(false); }
      }}>
        <Field label="What happened?" htmlFor="freeze-why">
          <Input id="freeze-why" required minLength={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Lost phone, a payment I don't recognise…" />
        </Field>
        <Button type="submit" variant="danger" size="lg" className="w-full" disabled={busy}>Freeze account</Button>
      </form>
    </Modal>
  );
}

export default function AccountPage() {
  const { id = "" } = useParams();
  const [dialog, setDialog] = useState<"" | "limits" | "alias" | "freeze">("");
  const q = useQuery({
    queryKey: ["account", id],
    queryFn: async () => {
      const [a, bal, entries] = await Promise.all([
        get<Account>(`/api/accounts/${id}`), get<Balance>(`/api/accounts/${id}/balance`), get<Entry[]>(`/api/accounts/${id}/transactions?limit=100`),
      ]);
      return { a, bal, entries };
    },
  });
  // The card on Home already has this account, so the header can be drawn (and the card can
  // grow into it) before the passbook has loaded.
  const known = queryClient.getQueryData<Account[]>(["accounts"])?.find((x) => x.account_id === id);
  const a = q.data?.a ?? known;
  if (!a) {
    if (q.isLoading) return <div className="space-y-4"><Skeleton className="h-8 w-40" /><Skeleton className="h-56 rounded-panel" /><Skeleton className="h-72 rounded-card" /></div>;
    return <Empty title="This account didn't load" text={(q.error as Error)?.message || "Check your connection and try again."} action={<Button onClick={() => q.refetch()}>Try again</Button>} />;
  }
  const bal = q.data?.bal;
  const available = bal?.available_balance ?? a.available_balance ?? 0;
  const onHold = (bal?.ledger_balance ?? a.ledger_balance ?? available) - available;
  const entries = q.data?.entries;
  const active = a.status === "ACTIVE";

  // Passbook, grouped by day.
  const groups: [string, Entry[]][] = [];
  for (const e of entries || []) {
    const d = day(e.created_at);
    const last = groups[groups.length - 1];
    if (last && last[0] === d) last[1].push(e);
    else groups.push([d, [e]]);
  }
  const facts: [string, string, string][] = [
    ["Per payment", inr(a.per_txn_limit), a.pending_per_txn_limit ? `${inr(a.pending_per_txn_limit)} from ${when(a.pending_limits_at)}` : ""],
    ["Per day", inr(a.daily_limit), a.pending_daily_limit ? `${inr(a.pending_daily_limit)} from ${when(a.pending_limits_at)}` : ""],
    ["Holders", (a.holders || []).map((x) => `${x.full_name}${x.role === "PRIMARY" ? "" : " (joint)"}`).join(", "), a.opened_at ? `Opened ${day(a.opened_at)}` : ""],
  ];

  return (
    <div>
      <Link to="/home" className="press mb-4 inline-flex items-center gap-1.5 rounded-full text-sm font-semibold text-muted hover:text-ink"><ArrowLeft className="size-4" /> Home</Link>
      <div className="items-start gap-8 lg:grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <div className="min-w-0 lg:sticky lg:top-10">
          <motion.div layoutId={accountLayoutId(a.account_id)} transition={spring.shared} className={cn("relative overflow-hidden rounded-panel p-6 sm:p-8", accountTone(a))}>
            <Mark variant="bare" title={null} className="pointer-events-none absolute -right-10 -bottom-14 size-[280px] opacity-10" />
            <div className="relative flex flex-wrap items-center gap-2 text-sm font-semibold">
              {PRODUCT[a.product_type]} account
              <StatusPill status={a.status} />
            </div>
            <p className="relative mt-8 font-display text-[40px] font-semibold leading-none tracking-[-0.04em] num sm:text-[52px]">
              <CountUp id="available" paise={available} />
            </p>
            <p className="relative mt-2 text-sm font-medium">
              Available{onHold > 0 ? `, ${inr(onHold)} on hold` : ""}{bal?.accrued_interest && bal.accrued_interest > 0 ? `, ${inr(Math.floor(bal.accrued_interest))} interest so far` : ""}
            </p>
            <button type="button" onClick={async () => { if (await copyText(a.account_number)) toast.success("Account number copied"); }}
              aria-label={`Copy account number ${groupDigits(a.account_number)}`}
              className="press relative mt-7 inline-flex items-center gap-2 rounded-full bg-current/12 px-3.5 py-1.5 text-sm font-semibold tracking-wider num hover:bg-current/20">
              <span className="text-current">{groupDigits(a.account_number)}</span> <Copy className="size-3.5" />
            </button>
          </motion.div>

          {a.status === "FROZEN" && (
            <Notice tone="bad" className="mt-4" icon={<Snowflake />} title="This account is frozen">
              {a.freeze_reason ? `Reason: ${a.freeze_reason}. ` : ""}Money can come in but not go out. Contact the branch to have it reviewed.
            </Notice>
          )}
          {a.status === "PENDING_KYC" && (
            <Notice tone="warn" className="mt-4" title="Waiting for KYC">
              This account becomes active when every holder's documents are accepted.{" "}
              <Link to="/profile#documents" className="font-semibold text-accent-text underline underline-offset-2">Upload your documents</Link>
            </Notice>
          )}

          {payable(a) && (
            <div className="mt-4 flex flex-wrap gap-2">
              {active && <Button asChild><Link to={`/pay?from=${a.account_id}`}><Send /> Pay from here</Link></Button>}
              {active && <Button asChild variant="secondary"><Link to={`/receive?account=${a.account_id}`}><QrCode /> Receive</Link></Button>}
              <Button variant="secondary" onClick={() => setDialog("alias")}><AtSign /> Pay ID</Button>
              <Button variant="secondary" onClick={() => setDialog("limits")}><Gauge /> Limits</Button>
              {active && <Button variant="danger-ghost" onClick={() => setDialog("freeze")}><Snowflake /> Freeze</Button>}
            </div>
          )}

          <dl className="mt-5 grid divide-y divide-line rounded-card bg-surface sm:grid-cols-3 sm:divide-x sm:divide-y-0">
            {facts.map(([k, v, s]) => (
              <div key={k} className="px-5 py-4">
                <dt className="text-[13px] text-muted">{k}</dt>
                <dd className="mt-0.5 font-semibold num">{v}</dd>
                {s && <dd className="text-[13px] text-muted">{s}</dd>}
              </div>
            ))}
          </dl>
        </div>

        <Section title="Passbook" className="lg:mt-0" action={
          <a href={`/classic/#/accounts/${a.account_id}`} className={cn(sectionLink, "inline-flex items-center gap-1")}>Statements and nominees <ExternalLink className="size-3.5" /></a>
        }>
          {!entries ? <Skeleton className="h-72 rounded-card" /> : groups.length ? (
            <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={spring.page}>
              <Card className="overflow-hidden">
                {groups.map(([d, list]) => (
                  <div key={d}>
                    <p className="bg-bg/60 px-5 py-2 text-xs font-semibold text-muted">{d}</p>
                    <ul className="divide-y divide-line">
                      {list.map((e, i) => (
                        <li key={e.entry_id || `${d}-${i}`} className="flex items-center gap-3.5 px-5 py-3.5">
                          <span className={cn("flex size-10 shrink-0 items-center justify-center rounded-full", e.amount > 0 ? "bg-success-soft text-success-text" : "bg-bg text-ink-2")}>
                            {e.amount > 0 ? <ArrowDownLeft className="size-5" /> : <ArrowUpRight className="size-5" />}
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block truncate font-semibold">{e.description || words(e.txn_type)}</span>
                            <span className="block truncate text-[13px] text-muted">{words(e.txn_type)}, {when(e.created_at)}</span>
                          </span>
                          <span className="text-right">
                            <span className={cn("block font-semibold num", e.amount > 0 && "text-success-text")}>{e.amount > 0 ? "+" : "−"}{inr(Math.abs(e.amount))}</span>
                            <span className="block text-[13px] text-muted num">{inr(e.balance_after)}</span>
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </Card>
            </motion.div>
          ) : (
            <Empty title="Nothing in the passbook yet" text="Deposit cash at the branch or ask someone to pay you."
              action={active ? <Button asChild variant="secondary"><Link to={`/receive?account=${a.account_id}`}>Show my QR code</Link></Button> : undefined} />
          )}
        </Section>
      </div>

      <Limits a={a} open={dialog === "limits"} onOpenChange={(o) => setDialog(o ? "limits" : "")} />
      <PayId a={a} open={dialog === "alias"} onOpenChange={(o) => setDialog(o ? "alias" : "")} />
      <Freeze a={a} open={dialog === "freeze"} onOpenChange={(o) => setDialog(o ? "freeze" : "")} />
    </div>
  );
}
