import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { toast } from "sonner";
import { Empty, PageHead, Section } from "@/components/AppShell";
import { Badge, Card, Skeleton } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/input";
import { api, ApiError, get, messageOf } from "@/lib/api";
import { queryClient, refreshMoney, useAccounts } from "@/lib/query";
import { withPinContext } from "@/lib/session";
import { payable, PRODUCT, type Deposit, type Quote, type Rates } from "@/lib/types";
import { cn, day, inr, pct, toPaise, uuid, words } from "@/lib/utils";

const span = (r: { min_days: number; max_days: number }) => {
  const f = (d: number) => (d >= 365 ? `${+(d / 365).toFixed(1)} yr` : `${d} days`);
  return `${f(r.min_days)} – ${f(r.max_days)}`;
};

function CloseEarly({ d, onClose }: { d: Deposit | null; onClose: () => void }) {
  const q = useQuery({ queryKey: ["closure", d?.deposit_id], queryFn: () => get(`/api/deposits/${d!.deposit_id}/closure-quote`), enabled: !!d });
  const [busy, setBusy] = useState(false);
  return (
    <Modal open={!!d} onOpenChange={(o) => !o && onClose()} title="Close this deposit early?"
      description={q.data ? `You've held it for ${q.data.held_days} days. Closing early pays a lower rate than the one you booked.` : "Working out what you'd get…"}>
      {q.data ? (
        <div className="space-y-5">
          <dl className="grid grid-cols-2 gap-3 text-[15px]">
            {[["Booked rate", pct(q.data.booked_rate)], ["Early-closure rate", pct(q.data.closure_rate)], ["You put in", inr(q.data.deposited)], ["If you keep it", inr(q.data.maturity_amount_if_kept)]].map(([k, v]) => (
              <div key={k} className="rounded-2xl bg-mist p-3"><dt className="text-[13px] text-muted">{k}</dt><dd className="font-semibold num">{v}</dd></div>
            ))}
          </dl>
          <Button variant="danger" size="lg" className="w-full" disabled={busy} onClick={async () => {
            setBusy(true);
            try {
              await withPinContext("Closing your deposit early", () => api("POST", `/api/deposits/${d!.deposit_id}/close`, undefined, { headers: { "Idempotency-Key": uuid() } }));
              toast.success("Deposit closed. The money is in your account.");
              queryClient.invalidateQueries({ queryKey: ["deposits"] }); refreshMoney(); onClose();
            } catch (e) { toast.error((e as Error).message); } finally { setBusy(false); }
          }}>Close and take {inr(q.data.payout_amount)}</Button>
        </div>
      ) : <Skeleton className="h-40" />}
    </Modal>
  );
}

export default function Deposits() {
  const accounts = useAccounts();
  const rates = useQuery<Rates>({ queryKey: ["rates"], queryFn: () => get("/api/deposits/rates") });
  const deposits = useQuery<Deposit[]>({ queryKey: ["deposits"], queryFn: () => get("/api/deposits") });
  const sources = useMemo(() => (accounts.data || []).filter((a) => payable(a) && a.status === "ACTIVE"), [accounts.data]);
  const [product, setProduct] = useState<"FD" | "RD">("FD");
  const [amount, setAmount] = useState("50000");
  const [months, setMonths] = useState(12);
  const [funding, setFunding] = useState("");
  const [quote, setQuote] = useState<{ q?: Quote; error?: string }>({});
  const [busy, setBusy] = useState(false);
  const [closing, setClosing] = useState<Deposit | null>(null);
  const key = useRef(uuid());
  const paise = toPaise(amount);

  useEffect(() => { if (!funding && sources[0]) setFunding(sources[0].account_id); }, [sources, funding]);
  useEffect(() => {
    key.current = uuid();
    if (!paise || !months) return setQuote({});
    let live = true;
    const t = setTimeout(() => get<Quote>(`/api/deposits/quote?product=${product}&amount=${paise}&tenure_months=${months}`)
      .then((q) => live && setQuote({ q })).catch((e) => live && setQuote({ error: e.message })), 250);
    return () => { live = false; clearTimeout(t); };
  }, [product, paise, months]);

  async function book(e: React.FormEvent) {
    e.preventDefault();
    if (!paise) return toast.error("Enter an amount in rupees.");
    setBusy(true);
    try {
      const r = await withPinContext(`Book a ${PRODUCT[product]!.toLowerCase()} of ${inr(paise)}`, () =>
        api<{ status: number; data: any }>("POST", `/api/deposits/${product.toLowerCase()}`, { funding_account_id: funding, amount: paise, tenure_months: months },
          { headers: { "Idempotency-Key": key.current }, full: true, accept: [422] }));
      if (r.data?.status === "FAILED") throw new Error(`We couldn't book this deposit: ${words(r.data.failure_code || "payment failed")}.`);
      if (r.status === 422) throw new ApiError(422, messageOf(r.data, 422));
      toast.success(`${PRODUCT[product]} booked: ${inr(paise)} at ${pct(r.data.annual_rate)}`);
      queryClient.invalidateQueries({ queryKey: ["deposits"] }); refreshMoney();
      key.current = uuid();
    } catch (ex) { toast.error((ex as Error).message); } finally { setBusy(false); }
  }

  const tenure = months >= 12 ? `${Math.floor(months / 12)} yr${months >= 24 ? "s" : ""}${months % 12 ? ` ${months % 12} mo` : ""}` : `${months} months`;

  return (
    <div>
      <PageHead title="Deposits" lead="Lock money away for a fixed term and earn more than a savings account." />
      <div className="grid items-start gap-6 lg:grid-cols-[1fr_380px]">
        <Card className="p-5 sm:p-6">
          <form id="deposit-form" onSubmit={book} className="space-y-6">
            <div className="relative grid grid-cols-2 rounded-full bg-mist p-1" role="group" aria-label="Deposit type">
              {(["FD", "RD"] as const).map((p) => (
                <button key={p} type="button" data-product={p} aria-pressed={product === p} onClick={() => setProduct(p)}
                  className={cn("relative z-10 rounded-full py-2.5 text-sm font-semibold transition-colors", product === p ? "text-ink" : "text-muted")}>
                  {product === p && <motion.span layoutId="dep-pill" className="absolute inset-0 -z-10 rounded-full bg-surface shadow-sm" transition={{ type: "spring", stiffness: 500, damping: 40 }} />}
                  {p === "FD" ? "Fixed deposit" : "Recurring deposit"}
                </button>
              ))}
            </div>
            <Field label={product === "FD" ? "Amount to deposit (₹)" : "Monthly instalment (₹)"} htmlFor="dep-amount">
              <Input id="dep-amount" name="amount" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} className="text-lg font-semibold num" />
            </Field>
            <div>
              <div className="mb-2 flex justify-between text-[13px] font-semibold text-ink-2"><label htmlFor="tenure">Term</label><span className="num">{tenure}</span></div>
              <input id="tenure" name="tenure" type="range" min={1} max={120} value={months} onChange={(e) => setMonths(+e.target.value)}
                className="w-full accent-[var(--cobalt)]" />
              <div className="mt-1 flex justify-between text-xs text-muted"><span>1 month</span><span>10 years</span></div>
            </div>
            {sources.length ? (
              <Field label="Pay from" htmlFor="funding">
                <Select id="funding" name="funding" value={funding} onChange={(e) => setFunding(e.target.value)}>
                  {sources.map((a) => <option key={a.account_id} value={a.account_id}>{PRODUCT[a.product_type]} ••{a.account_number.slice(-4)} · {inr(a.available_balance ?? 0)}</option>)}
                </Select>
              </Field>
            ) : <p className="rounded-2xl bg-warn-bg p-3 text-sm">You need an active savings or current account to fund a deposit.</p>}
            {sources.length > 0 && <Button type="submit" size="lg" className="w-full" disabled={busy || !quote.q}>{product === "FD" ? "Book fixed deposit" : "Start recurring deposit"}</Button>}
            <p className="text-center text-[13px] text-muted">Needs your 4-digit app PIN.</p>
          </form>
        </Card>

        <Card id="quote" aria-live="polite" className="relative overflow-hidden rounded-[30px] border-0 bg-cobalt-ink p-6 text-white">
          <p className="text-sm font-semibold text-white/60">You get back</p>
          {quote.q ? (
            <>
              <motion.p key={quote.q.maturity_amount} initial={{ opacity: 0.4, y: 6 }} animate={{ opacity: 1, y: 0 }} className="mt-1 font-display text-[40px] font-semibold tracking-tight num">
                {inr(quote.q.maturity_amount)}
              </motion.p>
              <p className="text-sm text-white/60">on {day(quote.q.maturity_date)}</p>
              <div className="mt-6 h-2.5 overflow-hidden rounded-full bg-white/10" aria-hidden>
                <motion.div className="h-full rounded-full bg-volt" animate={{ width: `${Math.min(100, (quote.q.interest / quote.q.maturity_amount) * 100 * 3)}%` }} />
              </div>
              <dl className="mt-6 space-y-3 text-[15px]">
                <div className="flex justify-between"><dt className="text-white/60">{product === "RD" ? "You put in" : "You deposit"}</dt><dd className="num">{inr(quote.q.total_deposited)}</dd></div>
                <div className="flex justify-between"><dt className="text-white/60">Interest earned</dt><dd className="font-semibold text-volt num">+{inr(quote.q.interest)}</dd></div>
                <div className="flex justify-between"><dt className="text-white/60">Rate</dt><dd className="num">{pct(quote.q.annual_rate)} a year</dd></div>
              </dl>
            </>
          ) : <p className="mt-2 text-white/70">{quote.error || "Enter an amount and a term to see what you'd earn."}</p>}
        </Card>
      </div>

      <Section title="Your deposits">
        {deposits.isLoading ? <Skeleton className="h-40" /> : deposits.data?.length ? (
          <div className="grid gap-3 sm:grid-cols-2">
            {deposits.data.map((d) => {
              const total = +new Date(d.maturity_date) - +new Date(d.start_date);
              const done = Math.max(0, Math.min(1, (+new Date(rates.data?.business_date || Date.now()) - +new Date(d.start_date)) / (total || 1)));
              const live = d.status === "ACTIVE";
              return (
                <Card key={d.deposit_id} data-deposit={d.deposit_id} className="p-5">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="font-semibold">{PRODUCT[d.product]}</p>
                      <p className="text-[13px] text-muted">{pct(d.annual_rate)} · {d.tenure_months} months · matures {day(d.maturity_date)}</p>
                    </div>
                    <Badge tone={live ? "good" : "neutral"}>{words(d.status)}</Badge>
                  </div>
                  <p className="mt-4 font-display text-2xl font-semibold tracking-tight num">{inr(live ? d.balance : d.payout_amount ?? d.balance)}</p>
                  <p className="text-[13px] text-muted">{live ? `grows to ${inr(d.projected_maturity)}` : d.payout_amount != null ? "paid out" : ""}
                    {d.product === "RD" ? ` · ${d.installments_paid} paid${d.installments_missed ? `, ${d.installments_missed} missed` : ""}` : ""}</p>
                  {live && (
                    <>
                      <div className="mt-4 h-2 overflow-hidden rounded-full bg-mist" role="img" aria-label={`${Math.round(done * 100)}% of the term done`}>
                        <motion.div className="h-full rounded-full bg-go" initial={{ width: 0 }} animate={{ width: `${Math.round(done * 100)}%` }} transition={{ duration: 0.8 }} />
                      </div>
                      <div className="mt-4 flex gap-2">
                        <Button size="sm" variant="secondary" onClick={() => setClosing(d)}>Close early</Button>
                        <Button size="sm" variant="ghost" asChild><a href={`/classic/#/accounts/${d.deposit_id}`}>Nominees</a></Button>
                      </div>
                    </>
                  )}
                </Card>
              );
            })}
          </div>
        ) : <Empty title="No deposits yet" text="Book one above. Interest is added every quarter." />}
      </Section>

      {rates.data && (
        <Section title="Today's rates" action={<span className="text-[13px] text-muted">Bank date {day(rates.data.business_date)}</span>}>
          <div className="grid gap-3 sm:grid-cols-2">
            {(["FD", "RD"] as const).map((p) => (
              <Card key={p} className="overflow-hidden">
                <p className="border-b border-line px-5 py-3 text-sm font-semibold">{PRODUCT[p]}</p>
                <ul className="divide-y divide-line/70 text-[15px]">
                  {rates.data!.rates.filter((r) => r.product === p).map((r) => (
                    <li key={`${r.min_days}`} className="flex justify-between px-5 py-2.5"><span className="text-ink-2">{span(r)}</span><span className="font-semibold num">{pct(r.annual_rate)}</span></li>
                  ))}
                </ul>
              </Card>
            ))}
          </div>
        </Section>
      )}
      <CloseEarly d={closing} onClose={() => setClosing(null)} />
    </div>
  );
}
