import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Copy, Send, QrCode, Snowflake, AtSign, Gauge, ExternalLink, ArrowDownLeft, ArrowUpRight } from "lucide-react";
import { toast } from "sonner";
import { Empty, Section } from "@/components/AppShell";
import { Mark } from "@/components/Logo";
import { Card, Skeleton } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/input";
import { api, get } from "@/lib/api";
import { queryClient } from "@/lib/query";
import { withPinContext } from "@/lib/session";
import { payable, PRODUCT, type Account, type Entry } from "@/lib/types";
import { copyText, day, groupDigits, inr, toPaise, when, words } from "@/lib/utils";

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
  if (q.isLoading) return <div className="space-y-4"><Skeleton className="h-8 w-40" /><Skeleton className="h-56 rounded-[28px]" /><Skeleton className="h-72" /></div>;
  if (q.error || !q.data) return <Empty title="This account didn't load" text={(q.error as Error)?.message || ""} action={<Button onClick={() => q.refetch()}>Try again</Button>} />;
  const { a, bal, entries } = q.data;
  const active = a.status === "ACTIVE";
  const onHold = bal.ledger_balance - bal.available_balance;

  // Passbook, grouped by day.
  const groups: [string, Entry[]][] = [];
  for (const e of entries) {
    const d = day(e.created_at);
    const last = groups[groups.length - 1];
    if (last && last[0] === d) last[1].push(e);
    else groups.push([d, [e]]);
  }

  return (
    <div>
      <Link to="/home" className="mb-5 inline-flex items-center gap-1.5 text-sm font-semibold text-muted hover:text-ink"><ArrowLeft className="size-4" /> Home</Link>
      <div className="relative overflow-hidden rounded-[30px] bg-cobalt p-6 text-white sm:p-8">
        <Mark tile={false} crop title={null} className="pointer-events-none absolute -right-8 -bottom-16 h-[260px] w-auto opacity-[0.12]" />
        <div className="relative flex flex-wrap items-center gap-2 text-sm font-semibold text-white/80">
          {PRODUCT[a.product_type]} account
          {a.status !== "ACTIVE" && <span className="rounded-full bg-white/15 px-2.5 py-0.5 text-xs">{words(a.status)}</span>}
        </div>
        <p id="available" className="relative mt-6 font-display text-[44px] font-semibold tracking-tight num sm:text-[52px]">{inr(bal.available_balance)}</p>
        <p className="relative text-sm text-white/70">
          Available{onHold > 0 ? ` · ${inr(onHold)} on hold` : ""}{bal.accrued_interest && bal.accrued_interest > 0 ? ` · ${inr(Math.floor(bal.accrued_interest))} interest so far` : ""}
        </p>
        <button type="button" onClick={async () => { if (await copyText(a.account_number)) toast.success("Account number copied"); }}
          className="relative mt-6 inline-flex items-center gap-2 rounded-full bg-white/12 px-3.5 py-1.5 text-sm font-semibold tracking-wider num hover:bg-white/20">
          {groupDigits(a.account_number)} <Copy className="size-3.5" />
        </button>
      </div>

      {a.status === "FROZEN" && (
        <div className="mt-4 rounded-3xl bg-bad-bg p-4 text-[15px]"><b>This account is frozen.</b> {a.freeze_reason ? `Reason: ${a.freeze_reason}. ` : ""}Money can come in but not go out. Contact the branch to have it reviewed.</div>
      )}
      {a.status === "PENDING_KYC" && (
        <div className="mt-4 rounded-3xl bg-warn-bg p-4 text-[15px]"><b>Waiting for KYC.</b> This account becomes active when every holder's documents are accepted. <Link to="/profile#documents" className="font-semibold text-cobalt">Upload your documents</Link></div>
      )}

      {payable(a) && (
        <div className="mt-5 flex flex-wrap gap-2">
          {active && <Button asChild><Link to={`/pay?from=${a.account_id}`}><Send /> Pay from here</Link></Button>}
          {active && <Button asChild variant="secondary"><Link to={`/receive?account=${a.account_id}`}><QrCode /> Receive</Link></Button>}
          <Button variant="secondary" onClick={() => setDialog("alias")}><AtSign /> Pay ID</Button>
          <Button variant="secondary" onClick={() => setDialog("limits")}><Gauge /> Limits</Button>
          {active && <Button variant="danger-ghost" onClick={() => setDialog("freeze")}><Snowflake /> Freeze</Button>}
        </div>
      )}

      <div className="mt-6 grid gap-3 sm:grid-cols-3">
        {[["Per payment", inr(a.per_txn_limit), a.pending_per_txn_limit ? `${inr(a.pending_per_txn_limit)} from ${when(a.pending_limits_at)}` : ""],
          ["Per day", inr(a.daily_limit), a.pending_daily_limit ? `${inr(a.pending_daily_limit)} from ${when(a.pending_limits_at)}` : ""],
          ["Holders", (a.holders || []).map((x) => `${x.full_name}${x.role === "PRIMARY" ? "" : " (joint)"}`).join(", "), a.opened_at ? `Opened ${day(a.opened_at)}` : ""]].map(([k, v, s]) => (
          <Card key={k} className="p-4">
            <p className="text-[13px] font-semibold text-muted">{k}</p>
            <p className="mt-1 font-semibold num">{v}</p>
            {s && <p className="text-[13px] text-muted">{s}</p>}
          </Card>
        ))}
      </div>

      <Section title="Passbook" action={
        <a href={`/classic/#/accounts/${a.account_id}`} className="inline-flex items-center gap-1 text-sm font-semibold text-cobalt">Statements & nominees <ExternalLink className="size-3.5" /></a>
      }>
        {groups.length ? (
          <Card className="overflow-hidden">
            {groups.map(([d, list]) => (
              <div key={d}>
                <p className="sticky top-[61px] z-10 bg-mist/95 px-5 py-2 text-xs font-semibold tracking-wide text-muted backdrop-blur lg:top-0">{d}</p>
                <ul className="divide-y divide-line/70">
                  {list.map((e, i) => (
                    <li key={e.entry_id || `${d}-${i}`} className="flex items-center gap-3.5 px-5 py-3.5">
                      <span className={e.amount > 0 ? "flex size-10 items-center justify-center rounded-full bg-good-bg text-go-deep" : "flex size-10 items-center justify-center rounded-full bg-mist text-ink-2"}>
                        {e.amount > 0 ? <ArrowDownLeft className="size-5" /> : <ArrowUpRight className="size-5" />}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-semibold">{e.description || words(e.txn_type)}</span>
                        <span className="block text-[13px] text-muted">{words(e.txn_type)} · {when(e.created_at)}</span>
                      </span>
                      <span className="text-right">
                        <span className={e.amount > 0 ? "block font-semibold text-go-deep num" : "block font-semibold num"}>{e.amount > 0 ? "+" : "−"}{inr(Math.abs(e.amount))}</span>
                        <span className="block text-[13px] text-muted num">{inr(e.balance_after)}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </Card>
        ) : (
          <Empty title="Nothing in the passbook yet" text="Deposit cash at the branch or ask someone to pay you."
            action={active ? <Button asChild variant="secondary"><Link to={`/receive?account=${a.account_id}`}>Show my QR code</Link></Button> : undefined} />
        )}
      </Section>

      <Limits a={a} open={dialog === "limits"} onOpenChange={(o) => setDialog(o ? "limits" : "")} />
      <PayId a={a} open={dialog === "alias"} onOpenChange={(o) => setDialog(o ? "alias" : "")} />
      <Freeze a={a} open={dialog === "freeze"} onOpenChange={(o) => setDialog(o ? "freeze" : "")} />
    </div>
  );
}
