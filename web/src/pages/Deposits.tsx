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
import { CountUp } from "@/components/ui/count-up";
import { spring } from "@/lib/motion";
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
              <div key={k} className="rounded-field bg-bg p-3"><dt className="text-[13px] text-muted">{k}</dt><dd className="font-semibold num">{v}</dd></div>
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
            <div className="relative grid grid-cols-2 rounded-full bg-bg p-1" role="group" aria-label="Deposit type">
              {(["FD", "RD"] as const).map((p) => (
                <button key={p} type="button" data-product={p} aria-pressed={product === p} onClick={() => setProduct(p)}
                  className={cn("press relative z-10 rounded-full py-2.5 text-sm font-semibold transition-colors duration-fast", product === p ? "text-on-night" : "text-ink-2 hover:text-ink")}>
                  {product === p && <motion.span layoutId="dep-pill" className="absolute inset-0 -z-10 rounded-full bg-night" transition={spring.snappy} />}
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
                className="w-full" />
              <div className="mt-1 flex justify-between text-xs text-muted"><span>1 month</span><span>10 years</span></div>
            </div>
            {sources.length ? (
              <Field label="Pay from" htmlFor="funding">
                <Select id="funding" name="funding" value={funding} onChange={(e) => setFunding(e.target.value)}>
                  {sources.map((a) => <option key={a.account_id} value={a.account_id}>{PRODUCT[a.product_type]} ••{a.account_number.slice(-4)}, {inr(a.available_balance ?? 0)} available</option>)}
                </Select>
              </Field>
            ) : <p className="rounded-field bg-warn-soft p-3 text-sm">You need an active savings or current account to fund a deposit.</p>}
            {sources.length > 0 && <Button type="submit" size="lg" className="w-full" disabled={busy || !quote.q}>{product === "FD" ? "Book fixed deposit" : "Start recurring deposit"}</Button>}
            <p className="text-center text-[13px] text-muted">Needs your 4-digit app PIN.</p>
          </form>
        </Card>

        <Card id="quote" aria-live="polite" className="on-dark relative overflow-hidden rounded-panel bg-night p-6 text-on-night lg:sticky lg:top-10">
          <p className="text-sm font-semibold text-on-night-muted">You get back</p>
          {quote.q ? (
            <>
              <p className="mt-1 font-display text-[36px] font-semibold tracking-[-0.04em] num sm:text-[40px]"><CountUp paise={quote.q.maturity_amount} /></p>
              <p className="text-sm text-on-night-muted">on {day(quote.q.maturity_date)}</p>
              {/* What you put in, and the interest on top of it, to scale. */}
              <div className="mt-6 flex h-3 gap-1" aria-hidden>
                <div className="h-full flex-1 rounded-full bg-on-night/30" />
                <motion.div className="h-full rounded-full bg-sun" animate={{ width: `${Math.max(3, (quote.q.interest / quote.q.maturity_amount) * 100)}%` }} transition={spring.page} />
              </div>
              <dl className="mt-6 space-y-3 text-[15px]">
                <div className="flex justify-between"><dt className="text-on-night-muted">{product === "RD" ? "You put in" : "You deposit"}</dt><dd className="num">{inr(quote.q.total_deposited)}</dd></div>
                <div className="flex justify-between"><dt className="text-on-night-muted">Interest earned</dt><dd className="font-semibold text-sun num">+{inr(quote.q.interest)}</dd></div>
                <div className="flex justify-between"><dt className="text-on-night-muted">Rate</dt><dd className="num">{pct(quote.q.annual_rate)} a year</dd></div>
              </dl>
            </>
          ) : <p className="mt-2 text-on-night-muted">{quote.error || "Enter an amount and a term to see what you'd earn."}</p>}
        </Card>
      </div>

      <Section title="Your deposits">
        {deposits.isLoading ? <Skeleton className="h-40 rounded-card" /> : deposits.data?.length ? (
          <Card className="overflow-hidden"><ul className="divide-y divide-line">
            {deposits.data.map((d) => {
              const total = +new Date(d.maturity_date) - +new Date(d.start_date);
              const done = Math.max(0, Math.min(1, (+new Date(rates.data?.business_date || Date.now()) - +new Date(d.start_date)) / (total || 1)));
              const live = d.status === "ACTIVE";
              return (
                <li key={d.deposit_id} data-deposit={d.deposit_id} className="grid gap-x-6 gap-y-3 p-5 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="font-semibold">{PRODUCT[d.product]} of {inr(d.principal)}{d.product === "RD" ? " a month" : ""}</p>
                      <Badge tone={live ? "good" : "neutral"}>{words(d.status)}</Badge>
                    </div>
                    <p className="mt-0.5 text-[13px] text-muted">{pct(d.annual_rate)} for {d.tenure_months} months, matures {day(d.maturity_date)}
                      {d.product === "RD" ? `. ${d.installments_paid} paid${d.installments_missed ? `, ${d.installments_missed} missed` : ""}` : ""}</p>
                    {live && (
                      <div className="mt-3 h-2 max-w-md overflow-hidden rounded-full bg-sunken" role="img" aria-label={`${Math.round(done * 100)}% of the term done`}>
                        <motion.div className="h-full rounded-full bg-night" initial={{ width: 0 }} animate={{ width: `${Math.max(2, Math.round(done * 100))}%` }} transition={{ ...spring.page, delay: 0.2 }} />
                      </div>
                    )}
                  </div>
                  <div className="sm:text-right">
                    <p className="font-display text-2xl font-semibold tracking-[-0.03em] num">{inr(live ? d.balance : d.payout_amount ?? d.balance)}</p>
                    <p className="text-[13px] text-muted">{live ? `grows to ${inr(d.projected_maturity)}` : d.payout_amount != null ? "paid out" : ""}</p>
                    {live && (
                      <div className="mt-2 flex gap-2 sm:justify-end">
                        <Button size="sm" variant="secondary" onClick={() => setClosing(d)}>Close early</Button>
                        <Button size="sm" variant="ghost" asChild><a href={`/classic/#/accounts/${d.deposit_id}`}>Nominees</a></Button>
                      </div>
                    )}
                  </div>
                </li>
              );
            })}
          </ul></Card>
        ) : <Empty title="No deposits yet" text="Book one above. Interest is added every quarter." />}
      </Section>

      {rates.data && (
        <Section title="Today's rates" action={<span className="text-[13px] text-muted">Bank date {day(rates.data.business_date)}</span>}>
          <Card className="grid divide-y divide-line overflow-hidden sm:grid-cols-2 sm:divide-x sm:divide-y-0">
            {(["FD", "RD"] as const).map((p) => (
              <table key={p} className="w-full text-[15px]">
                <caption className="px-5 pt-4 pb-2 text-left text-sm font-semibold">{PRODUCT[p]}</caption>
                <thead className="sr-only"><tr><th>Term</th><th>Rate a year</th></tr></thead>
                <tbody>
                  {rates.data!.rates.filter((r) => r.product === p).map((r) => (
                    <tr key={`${r.min_days}`} className="border-t border-line"><td className="px-5 py-2.5 text-ink-2">{span(r)}</td><td className="px-5 py-2.5 text-right font-semibold num">{pct(r.annual_rate)}</td></tr>
                  ))}
                </tbody>
              </table>
            ))}
          </Card>
        </Section>
      )}
      <CloseEarly d={closing} onClose={() => setClosing(null)} />
    </div>
  );
}
