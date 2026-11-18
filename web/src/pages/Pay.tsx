import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { motion } from "motion/react";
import { AlertCircle, BadgeCheck, Loader2, Plus, QrCode, ScanLine, Trash2, UserPlus, X } from "lucide-react";
import { toast } from "sonner";
import { Empty, PageHead, Section, sectionLink } from "@/components/AppShell";
import { Avatar, PaymentRow, PaymentSheet, Slip } from "@/components/Payment";
import { Scanner } from "@/components/Scanner";
import { AmountEntry } from "@/components/AmountEntry";
import { Journey } from "@/components/Journey";
import { SwipeButton } from "@/components/SwipeButton";
import { Badge, Card, Skeleton } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/input";
import { api, ApiError, get, messageOf } from "@/lib/api";
import { spring } from "@/lib/motion";
import type { PayCode } from "@/lib/paycode";
import { queryClient, refreshMoney, useAccounts, useBeneficiaries, usePayments } from "@/lib/query";
import { withPinContext } from "@/lib/session";
import { payable, PRODUCT, type Payment } from "@/lib/types";
import { cn, groupDigits, inr, toPaise, uuid, when } from "@/lib/utils";

type PayeeKind = "ACCOUNT_NUMBER" | "PAY_ID" | "MOBILE";
type Payee = { value: string; name: string; number: string; kind: PayeeKind };
const KIND_LABEL: Record<PayeeKind, string> = { ACCOUNT_NUMBER: "Account number", PAY_ID: "Pay ID", MOBILE: "Mobile number" };
const looksLikePayee = (v: string) => /^\d{12}$/.test(v) || v.includes("@") || /^(\+91|0)?[6-9]\d{9}$/.test(v);

function AddPayee({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const [number, setNumber] = useState("");
  const [nickname, setNickname] = useState("");
  const [holder, setHolder] = useState<{ name?: string; error?: string }>({});
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const n = number.replace(/\s/g, "");
    if (n.length < 12) return setHolder({});
    let live = true;
    get(`/api/payees/${encodeURIComponent(n)}`)
      .then((r) => live && setHolder({ name: r.display_name }))
      .catch((e) => live && setHolder({ error: e.status === 404 ? "No account matches that number." : e.message }));
    return () => { live = false; };
  }, [number]);
  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Add a payee"
      description="For the first 24 hours a new payee can receive up to ₹10,000 a payment: if someone else added them, you have a day to notice.">
      <form className="space-y-5" onSubmit={async (e) => {
        e.preventDefault(); setBusy(true);
        try {
          const body: Record<string, string> = { payee_account_number: number.replace(/\s/g, "") };
          if (nickname.trim()) body.nickname = nickname.trim();
          const r = await withPinContext(`Saving ${holder.name || "this payee"}`, () => api("POST", "/api/me/beneficiaries", body));
          toast.success(`${r.nickname || r.payee_name} saved. Full limits from ${when(r.cooling_until)}.`);
          queryClient.invalidateQueries({ queryKey: ["beneficiaries"] });
          onOpenChange(false); setNumber(""); setNickname("");
        } catch (ex) { toast.error((ex as Error).message); } finally { setBusy(false); }
      }}>
        <Field label="Account number" htmlFor="ap-number" error={holder.error}
          hint={holder.name ? <span className="inline-flex items-center gap-1 font-semibold text-success-text"><BadgeCheck className="size-4" /> {holder.name}</span> : "We'll show you the name before saving."}>
          <Input id="ap-number" inputMode="numeric" required autoComplete="off" placeholder="12-digit account number" value={number} onChange={(e) => setNumber(e.target.value)} />
        </Field>
        <Field label={<>Nickname <span className="font-normal text-muted">(optional)</span></>} htmlFor="ap-nick">
          <Input id="ap-nick" maxLength={40} autoComplete="off" placeholder="Landlord, Mum…" value={nickname} onChange={(e) => setNickname(e.target.value)} />
        </Field>
        <Button type="submit" size="lg" className="w-full" disabled={busy || !holder.name}>Save payee</Button>
      </form>
    </Modal>
  );
}

export default function Pay() {
  const [params] = useSearchParams();
  const { hash } = useLocation();
  const accounts = useAccounts();
  const saved = useBeneficiaries();
  const history = usePayments(30);
  const sources = useMemo(() => (accounts.data || []).filter((a) => payable(a) && a.status === "ACTIVE"), [accounts.data]);

  const [from, setFrom] = useState(params.get("from") || "");
  const [to, setTo] = useState(params.get("to") || "");
  const [payee, setPayee] = useState<Payee | null>(null);
  const [lookup, setLookup] = useState<{ busy?: boolean; error?: string }>({});
  const [amount, setAmount] = useState(params.get("amount") || "");
  const [note, setNote] = useState(params.get("note") || "");
  const [channel, setChannel] = useState(params.get("to") ? "QR_PAYMENT" : "TRANSFER");
  const [result, setResult] = useState<Payment | null>(null);
  const [journey, setJourney] = useState<Payment | null>(null);
  const [picked, setPicked] = useState("");    // the person tapped in "recent": their avatar flies into place
  const [error, setError] = useState("");
  const [addPayee, setAddPayee] = useState(false);
  const [opened, setOpened] = useState<Payment | null>(null);
  const [scan, setScan] = useState(params.get("scan") === "1");
  const key = useRef(uuid());           // one reference per attempt: a retry reuses it
  const amountRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!from && sources[0]) setFrom(sources.find((a) => a.account_id === params.get("from"))?.account_id || sources[0].account_id);
  }, [sources, from, params]);
  useEffect(() => { key.current = uuid(); }, [to, amount, from, note]);
  useEffect(() => { if (hash === "#payees") document.getElementById("payees")?.scrollIntoView({ behavior: "smooth" }); }, [hash]);

  // Look the payee up as they type: an account number, a pay ID or a mobile number, in one box.
  useEffect(() => {
    const v = to.trim().replace(/[\s-]/g, "");
    setPayee(null);
    if (!v || !looksLikePayee(v)) return setLookup({});
    let live = true;
    setLookup({ busy: true });
    const t = setTimeout(async () => {
      try {
        const r = await get(`/api/payees/lookup/${encodeURIComponent(v.includes("@") ? v.toLowerCase() : v)}`);
        if (!live) return;
        if (!r.can_receive) setLookup({ error: `${r.display_name}'s account can't receive payments. If someone asked you to pay it, it may be a scam.` });
        else { setPayee({ value: v.includes("@") ? v.toLowerCase() : v, name: r.display_name, number: r.account_number, kind: r.kind || "ACCOUNT_NUMBER" }); setLookup({}); }
      } catch (e) {
        const err = e as ApiError;
        if (live) setLookup({ error: err.status === 404 ? (/^(\+91|0)?[6-9]\d{9}$/.test(v) ? "No one receives payments on this mobile number." : "No account matches that. Check the number or pay ID.")
          : err.status === 429 ? "You've checked a lot of names just now. Wait a minute, or pay a saved payee." : err.message });
      }
    }, 350);
    return () => { live = false; clearTimeout(t); };
  }, [to]);

  const paise = toPaise(amount);
  const source = sources.find((a) => a.account_id === from);
  const ready = !!payee && !!paise && !!source;

  async function pay() {
    if (!ready) return;
    setError("");
    try {
      const body: Record<string, unknown> = { from_account_id: from, payee: payee!.value, amount: paise, channel };
      if (note.trim()) body.note = note.trim();
      const r = await withPinContext(`Pay ${inr(paise)} to ${payee!.name}`, () =>
        api<{ status: number; data: any }>("POST", "/api/payments", body, { headers: { "Idempotency-Key": key.current }, full: true, accept: [422] }));
      if (!r.data?.txn_id) throw new ApiError(r.status, messageOf(r.data, r.status));
      setJourney(r.data);          // the payment's trip through the six checks, then the slip
      key.current = uuid();
      refreshMoney();
    } catch (ex) {
      const e = ex as ApiError;
      setError(e.status === 0 || e.status >= 500 ? `${e.message} It's safe to try again: the same payment can't go through twice.` : e.message);
    }
  }

  /** A scanned or uploaded QR code fills the form in, exactly as opening a pay link does. */
  function scanned(code: PayCode) {
    setResult(null); setError("");
    setTo(code.to);
    setAmount(code.amount);
    setNote(code.note);
    setChannel("QR_PAYMENT");
    toast.success("QR code read. Check the name before you pay.");
    if (!code.amount) setTimeout(() => amountRef.current?.focus(), 450);
  }

  function reset(keepPayee = false) {
    setResult(null); setJourney(null); setAmount(""); setNote(""); setError("");
    if (!keepPayee) { setTo(""); setPayee(null); }
    setChannel("TRANSFER");
  }

  // Recent people: saved payees first, then whoever was paid lately.
  const recent = useMemo(() => {
    const seen = new Set<string>();
    const out: { number: string; name: string }[] = [];
    for (const b of saved.data || []) if (!seen.has(b.payee_account_number)) { seen.add(b.payee_account_number); out.push({ number: b.payee_account_number, name: b.nickname || b.payee_name }); }
    for (const p of history.data || []) {
      const n = p.to_account_number;
      if (n && p.status === "COMPLETED" && !seen.has(n)) { seen.add(n); out.push({ number: n, name: p.to_display_name || n }); }
    }
    return out.slice(0, 8);
  }, [saved.data, history.data]);

  if (accounts.isLoading) return <div className="space-y-4"><Skeleton className="h-9 w-24" /><Skeleton className="h-[460px] rounded-panel lg:w-[460px]" /></div>;
  if (!sources.length) {
    return (
      <div>
        <PageHead title="Pay" />
        <Empty title="You need an active account to pay from"
          text={(accounts.data || []).some(payable) ? "Your account is waiting for KYC or is frozen." : "Open a savings or current account first."}
          action={<Button asChild><Link to="/home">Go to Home</Link></Button>} />
      </div>
    );
  }

  return (
    <div>
      <PageHead title="Pay" lead="Anyone at Unbeatable Bank, by account number, pay ID, mobile number or QR code."
        actions={<>
          <Button variant="night" size="sm" id="scan" onClick={() => setScan(true)}><ScanLine /> Scan a QR code</Button>
          <Button asChild variant="secondary" size="sm"><Link to="/receive"><QrCode /> Receive</Link></Button>
        </>} />

      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-8 lg:grid-cols-[minmax(0,460px)_minmax(0,1fr)]">
        <Card className="relative overflow-hidden rounded-panel p-4 sm:p-6" id="pay-card">
          {journey && !result ? (
            <Journey p={journey} onDone={() => { setResult(journey); setJourney(null); }} />
          ) : result ? (
            <motion.div key="result" id="payment-result" aria-live="polite" className="print-in">
              <Slip p={result} fresh onChange={setResult} />
              <div className="mt-6 grid grid-cols-2 gap-3">
                <Button variant="secondary" size="lg" onClick={() => reset(true)}>Pay {payee?.name.split(" ")[0] || "them"} again</Button>
                <Button variant="night" size="lg" onClick={() => reset()}>Done</Button>
              </div>
            </motion.div>
          ) : (
            <form key="form" id="pay-form" onSubmit={(e) => { e.preventDefault(); pay(); }}>
              {/* -------------------------------- who */}
                {payee ? (
                  <motion.div key="payee" initial={{ opacity: 0, scale: 0.92 }} animate={{ opacity: 1, scale: 1 }} transition={spring.bouncy}
                    className="flex items-center gap-3 rounded-card bg-bg p-3 pr-2">
                    <motion.span layoutId={picked === payee.number ? `who-${payee.number}` : undefined} transition={spring.shared}><Avatar name={payee.name} /></motion.span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-semibold">{payee.name}</p>
                      <p className="flex items-center gap-1 truncate text-[13px] font-medium text-success-text num"><BadgeCheck className="size-3.5 shrink-0" /> {payee.kind === "ACCOUNT_NUMBER" ? groupDigits(payee.value) : payee.kind === "MOBILE" ? `+91 ${payee.value.slice(-10, -5)} ${payee.value.slice(-5)}` : payee.value}</p>
                      <span className="mt-1 inline-flex rounded-full bg-surface px-2 py-0.5 text-[11px] font-semibold text-ink-2">{channel === "QR_PAYMENT" ? "From a QR code" : KIND_LABEL[payee.kind]}{payee.kind !== "ACCOUNT_NUMBER" ? `, account ••${payee.number.slice(-4)}` : ""}</span>
                    </div>
                    <button type="button" aria-label="Change who you're paying" onClick={() => { setTo(""); setPayee(null); setChannel("TRANSFER"); }} className="press rounded-full p-2 text-muted hover:bg-surface hover:text-ink"><X className="size-5" /></button>
                  </motion.div>
                ) : (
                  <motion.div key="lookup" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                    <label htmlFor="payee" className="mb-1.5 block text-[13px] font-semibold text-ink-2">To</label>
                    <div className="relative">
                      <Input id="payee" name="payee" autoComplete="off" placeholder="Account number, pay ID or mobile" value={to} className="pr-14"
                        onChange={(e) => { setTo(e.target.value); setChannel("TRANSFER"); }} autoFocus={!params.get("to") && !scan} aria-invalid={!!lookup.error} aria-describedby="payee-help" />
                      {lookup.busy ? <Loader2 className="absolute top-1/2 right-4 size-5 -translate-y-1/2 animate-spin text-muted" /> : (
                        <button type="button" aria-label="Scan a QR code" onClick={() => setScan(true)} className="press absolute top-1/2 right-1.5 flex size-9 -translate-y-1/2 items-center justify-center rounded-full bg-night text-on-night hover:bg-night-2">
                          <ScanLine className="size-[18px]" />
                        </button>
                      )}
                    </div>
                    <p role="status" id="payee-help" className={cn("mt-1.5 text-[13px]", lookup.error ? "font-medium text-danger" : "text-muted")}>
                      {lookup.error || "We'll show you the name before any money moves."}
                    </p>
                    {recent.length > 0 && (
                      <div className="-mx-1 mt-3 flex gap-1 overflow-x-auto pb-1 no-scrollbar">
                        {recent.map((r) => (
                          <button key={r.number} type="button" onClick={() => { setPicked(r.number); setTo(r.number); setTimeout(() => amountRef.current?.focus(), 400); }}
                            className="press flex w-[68px] shrink-0 flex-col items-center gap-1.5 rounded-field p-1.5 hover:bg-bg">
                            <motion.span layoutId={`who-${r.number}`} transition={spring.shared}><Avatar name={r.name} className="size-12" /></motion.span>
                            <span className="w-full truncate text-center text-xs font-medium text-ink-2">{r.name.split(" ")[0]}</span>
                          </button>
                        ))}
                      </div>
                    )}
                  </motion.div>
                )}

              {/* -------------------------------- how much */}
              <div className="mt-7 text-center">
                <p className="text-[13px] font-semibold text-muted" aria-hidden>Amount</p>
                <div className="mt-2"><AmountEntry ref={amountRef} value={amount} onChange={setAmount} /></div>
                <div className="mt-3 flex justify-center gap-2">
                  {[100, 500, 1000, 2000].map((v) => (
                    <button key={v} type="button" onClick={() => setAmount(String(v))} className="press rounded-full bg-bg px-3 py-1.5 text-[13px] font-semibold text-ink-2 hover:bg-sunken hover:text-ink num">
                      ₹{v.toLocaleString("en-IN")}
                    </button>
                  ))}
                </div>
              </div>

              <div className="mt-7 space-y-4">
                <Input name="note" maxLength={140} placeholder="Add a note (rent, dinner, thanks…)" value={note} onChange={(e) => setNote(e.target.value)} aria-label="Note" />
                <Field label="From" htmlFor="from">
                  <Select id="from" name="from" value={from} onChange={(e) => setFrom(e.target.value)}>
                    {sources.map((a) => (
                      <option key={a.account_id} value={a.account_id}>
                        {PRODUCT[a.product_type]} ••{a.account_number.slice(-4)}, {inr(a.available_balance ?? 0)} available
                      </option>
                    ))}
                  </Select>
                </Field>
              </div>

              {error && (
                <p role="alert" className="help bad mt-4 flex gap-2 rounded-field bg-danger-soft p-3 text-sm font-medium text-danger"><AlertCircle className="mt-0.5 size-4 shrink-0" />{error}</p>
              )}
              {source && paise && paise > (source.available_balance ?? 0) && (
                <p className="mt-4 text-center text-sm font-medium text-warn">That's more than the {inr(source.available_balance ?? 0)} available in this account.</p>
              )}

              <div className="mt-6">
                <SwipeButton label={ready ? `Swipe to pay ${inr(paise)}` : payee ? "Enter an amount" : "Choose who to pay"} disabled={!ready} onConfirm={pay} />
                <p className="mt-3 text-center text-[13px] text-muted">Needs your 6-digit payment PIN.</p>
                <button type="submit" className="sr-only" tabIndex={-1}>Pay</button>
              </div>
            </form>
          )}
        </Card>

        <div>
          <Section className="mt-0" title="Saved payees" action={<Button variant="ghost" size="sm" id="add-payee" onClick={() => setAddPayee(true)}><UserPlus /> Add a payee</Button>}>
            <div id="payees" className="scroll-mt-24">
              {saved.isLoading ? <Skeleton className="h-32 rounded-card" /> : saved.data?.length ? (
                <Card className="overflow-hidden"><ul className="divide-y divide-line">
                  {saved.data.map((b) => (
                    <li key={b.beneficiary_id} className="flex items-center gap-3.5 px-4 py-3 sm:px-5">
                      <Avatar name={b.nickname || b.payee_name} />
                      <div className="min-w-0 flex-1">
                        <p className="truncate font-semibold">{b.nickname || b.payee_name}</p>
                        <p className="truncate text-[13px] text-muted num">{b.nickname ? `${b.payee_name}, ` : ""}{groupDigits(b.payee_account_number)}</p>
                        {b.in_cooling_period && <Badge tone="warn" className="mt-1 max-w-full whitespace-normal">New: up to ₹10,000 a payment until {when(b.cooling_until)}</Badge>}
                      </div>
                      <Button size="sm" variant="secondary" onClick={() => { reset(); setTo(b.payee_account_number); document.getElementById("pay-card")?.scrollIntoView({ behavior: "smooth", block: "start" }); }}>Pay</Button>
                      <button type="button" aria-label={`Remove ${b.nickname || b.payee_name}`} className="press rounded-full p-2 text-muted hover:bg-danger-soft hover:text-danger" onClick={async () => {
                        try { await api("DELETE", `/api/me/beneficiaries/${b.beneficiary_id}`); toast.success("Payee removed"); queryClient.invalidateQueries({ queryKey: ["beneficiaries"] }); }
                        catch (e) { toast.error((e as Error).message); }
                      }}><Trash2 className="size-4" /></button>
                    </li>
                  ))}
                </ul></Card>
              ) : (
                <Empty title="No saved payees" text="Save the people you pay often. A new payee can receive up to ₹10,000 a payment for the first 24 hours."
                  action={<Button variant="secondary" onClick={() => setAddPayee(true)}><Plus /> Add a payee</Button>} />
              )}
            </div>
          </Section>
          <Section title="Recent payments" action={<Link to="/activity" className={sectionLink}>See all</Link>}>
            {history.data?.length ? (
              <Card className="overflow-hidden" id="payment-history"><ul className="divide-y divide-line">{history.data.slice(0, 5).map((p) => <PaymentRow key={p.txn_id} p={p} onOpen={setOpened} />)}</ul></Card>
            ) : <Empty title="No payments yet" text="Your first payment will appear here." />}
          </Section>
        </div>
      </div>
      <Scanner open={scan} onOpenChange={setScan} onCode={scanned} />
      <AddPayee open={addPayee} onOpenChange={setAddPayee} />
      <PaymentSheet p={opened} onClose={() => setOpened(null)} />
    </div>
  );
}
