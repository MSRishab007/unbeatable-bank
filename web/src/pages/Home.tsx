import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { motion } from "motion/react";
import { Eye, EyeOff, Plus, QrCode, Send, PiggyBank, Users, ArrowRight, BadgeAlert } from "lucide-react";
import { toast } from "sonner";
import { Mark } from "@/components/Logo";
import { Empty, Section } from "@/components/AppShell";
import { PaymentRow, PaymentSheet } from "@/components/Payment";
import { Card, Skeleton } from "@/components/ui/card";
import { Modal } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { queryClient, useAccounts, useMe, usePayments } from "@/lib/query";
import { payable, PRODUCT, type Account, type Me, type Payment } from "@/lib/types";
import { cn, groupDigits, inr } from "@/lib/utils";

const hour = new Date().getHours();
const GREETING = hour < 5 ? "Up late" : hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";

export function KycNotice({ me }: { me?: Me }) {
  if (!me || me.kyc_status === "VERIFIED") return null;
  return (
    <div className="mb-6 flex gap-3 rounded-3xl border border-[#f3d27a] bg-warn-bg p-4 sm:p-5">
      <BadgeAlert className="mt-0.5 size-5 shrink-0 text-warn" />
      <div className="text-[15px]">
        <p className="font-semibold">{me.kyc_status === "REJECTED" ? "Your KYC was not accepted" : "Your KYC is waiting for the branch"}</p>
        <p className="mt-0.5 text-ink-2">
          You can open accounts now; they become active once your documents are accepted.{" "}
          <Link to="/profile#documents" className="font-semibold text-cobalt">Upload your documents</Link>, or quote customer number{" "}
          <b className="num">{me.cif_number}</b> at a branch.
        </p>
      </div>
    </div>
  );
}

export function OpenAccount({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const nav = useNavigate();
  const [product, setProduct] = useState("SAVINGS");
  const [joint, setJoint] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Open an account" description="It gets its own account number straight away. Hold as many as you like.">
      <form className="space-y-5" onSubmit={async (e) => {
        e.preventDefault(); setBusy(true);
        try {
          const body: Record<string, unknown> = { product_type: product };
          if (joint.trim()) body.joint_holder_ids = [joint.trim()];
          const a = await api("POST", "/api/accounts", body);
          toast.success(a.status === "ACTIVE" ? `Account ${a.account_number} is open` : `Account ${a.account_number} opens once KYC is verified`);
          queryClient.invalidateQueries({ queryKey: ["accounts"] });
          onOpenChange(false);
          nav(`/accounts/${a.account_id}`);
        } catch (ex) { toast.error((ex as Error).message); } finally { setBusy(false); }
      }}>
        <div className="grid grid-cols-2 gap-3" role="radiogroup" aria-label="Account type">
          {[["SAVINGS", "Savings", "Earns interest on the daily balance"], ["CURRENT", "Current", "For business, can carry an overdraft"]].map(([v, t, d]) => (
            <button key={v} type="button" role="radio" aria-checked={product === v} onClick={() => setProduct(v!)}
              className={cn("rounded-3xl border-2 p-4 text-left transition-colors", product === v ? "border-cobalt bg-cobalt/5" : "border-line hover:border-ink/20")}>
              <p className="font-semibold">{t}</p>
              <p className="mt-1 text-[13px] text-muted">{d}</p>
            </button>
          ))}
        </div>
        <Field label={<>Joint holder <span className="font-normal text-muted">(optional)</span></>} hint="Their customer ID, from their Profile page. Either holder can operate the account." htmlFor="joint">
          <Input id="joint" value={joint} onChange={(e) => setJoint(e.target.value)} placeholder="Customer ID" autoComplete="off" />
        </Field>
        <Button type="submit" size="lg" className="w-full" disabled={busy}>Open account</Button>
      </form>
    </Modal>
  );
}

function AccountCard({ a, hidden, index }: { a: Account; hidden: boolean; index: number }) {
  const onHold = a.available_balance != null && a.ledger_balance != null ? a.ledger_balance - a.available_balance : 0;
  return (
    <motion.div initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: index * 0.06 }} className="w-[86%] shrink-0 snap-start sm:w-[340px]">
      <Link to={`/accounts/${a.account_id}`} className={cn(
        "cover group relative flex h-[200px] flex-col overflow-hidden rounded-[28px] p-6 text-white transition-transform hover:-translate-y-0.5",
        a.product_type === "CURRENT" ? "bg-cobalt-ink" : "bg-cobalt", a.status === "FROZEN" && "saturate-0",
      )}>
        <Mark tile={false} crop title={null} className="pointer-events-none absolute -right-6 -bottom-10 h-[190px] w-auto opacity-[0.13] transition-transform duration-500 group-hover:rotate-6" />
        <div className="relative flex items-center justify-between text-sm font-semibold text-white/80">
          <span>{PRODUCT[a.product_type]}{a.my_role === "JOINT" ? " · joint" : ""}</span>
          {a.status !== "ACTIVE" && <span className="rounded-full bg-white/15 px-2.5 py-0.5 text-xs">{a.status === "PENDING_KYC" ? "Waiting for KYC" : a.status === "FROZEN" ? "Frozen" : a.status}</span>}
        </div>
        <p className="balance relative mt-auto font-display text-[34px] font-semibold tracking-tight num">
          {a.available_balance == null ? "—" : hidden ? "₹ ••••••" : inr(a.available_balance)}
        </p>
        <div className="relative mt-1 flex items-center justify-between text-[13px] text-white/70">
          <span className="acct num tracking-wider">{groupDigits(a.account_number)}</span>
          <span>{onHold > 0 && !hidden ? `${inr(onHold)} on hold` : "Available"}</span>
        </div>
      </Link>
    </motion.div>
  );
}

export default function Home() {
  const me = useMe();
  const accounts = useAccounts();
  const payments = usePayments(6);
  const [hidden, setHidden] = useState(false);
  const [openNew, setOpenNew] = useState(false);
  const [opened, setOpened] = useState<Payment | null>(null);
  const everyday = (accounts.data || []).filter(payable);
  const total = everyday.reduce((t, a) => t + (a.available_balance || 0), 0);
  const first = me.data?.full_name.split(" ")[0];

  const actions = [
    { to: "/pay", label: "Pay", icon: Send },
    { to: "/receive", label: "Receive", icon: QrCode },
    { to: "/pay#payees", label: "Payees", icon: Users },
    { to: "/deposits", label: "Deposit", icon: PiggyBank },
  ];

  return (
    <div>
      <div className="mb-6 flex items-end justify-between gap-4">
        <div>
          <p className="text-[15px] text-muted">{GREETING}{first ? "," : ""}</p>
          <h1 className="font-display text-[30px] font-semibold tracking-[-0.03em] sm:text-[36px]">{first || <Skeleton className="h-9 w-40" />}</h1>
        </div>
        <button type="button" onClick={() => setHidden(!hidden)} className="mb-1 inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm font-semibold text-ink-2 hover:bg-ink/5"
          aria-pressed={hidden}>
          {hidden ? <Eye className="size-4" /> : <EyeOff className="size-4" />} {hidden ? "Show" : "Hide"} balances
        </button>
      </div>

      <KycNotice me={me.data} />

      {everyday.length > 1 && (
        <p className="mb-3 text-sm text-muted">Across your accounts: <b className="font-semibold text-ink num">{hidden ? "₹ ••••••" : inr(total)}</b></p>
      )}
      {accounts.isLoading && <Skeleton className="h-[200px] w-[86%] rounded-[28px] sm:w-[340px]" />}
      {!accounts.isLoading && <div className="-mx-4 flex snap-x snap-mandatory scroll-px-4 gap-4 overflow-x-auto px-4 pb-2 [scrollbar-width:none] sm:-mx-6 sm:scroll-px-6 sm:px-6 lg:mx-0 lg:scroll-px-0 lg:px-0">
        {everyday.map((a, i) => <AccountCard key={a.account_id} a={a} hidden={hidden} index={i} />)}
        <button type="button" onClick={() => setOpenNew(true)}
          className="cover add flex h-[200px] w-[60%] shrink-0 snap-start flex-col items-center justify-center gap-2 rounded-[28px] border-2 border-dashed border-ink/15 text-ink-2 transition-colors hover:border-cobalt hover:text-cobalt sm:w-[200px]">
          <Plus className="size-7" />
          <span className="font-semibold">{everyday.length ? "Open another account" : "Open your first account"}</span>
        </button>
      </div>}

      <div className="mt-7 grid grid-cols-4 gap-2 sm:max-w-md">
        {actions.map(({ to, label, icon: Icon }) => (
          <Link key={label} to={to} className="group flex flex-col items-center gap-2 rounded-3xl py-2 text-[13px] font-semibold text-ink-2">
            <span className="flex size-14 items-center justify-center rounded-[20px] bg-surface text-cobalt shadow-[0_1px_0_rgba(13,15,43,.04),0_8px_20px_-12px_rgba(13,15,43,.25)] transition-transform group-hover:-translate-y-0.5 group-active:scale-95">
              <Icon className="size-6" />
            </span>
            {label}
          </Link>
        ))}
      </div>

      <Section title="Recent payments" action={payments.data?.length ? <Link to="/activity" className="inline-flex items-center gap-1 text-sm font-semibold text-cobalt">See all <ArrowRight className="size-4" /></Link> : null}>
        {payments.isLoading ? <Skeleton className="h-48" /> : payments.data?.length ? (
          <Card className="overflow-hidden"><ul className="divide-y divide-line/70">{payments.data.map((p) => <PaymentRow key={p.txn_id} p={p} onOpen={setOpened} />)}</ul></Card>
        ) : (
          <Empty title="No payments yet" text="Money you send shows up here, with the checks it went through." action={<Button asChild variant="secondary"><Link to="/pay">Make a payment</Link></Button>} />
        )}
      </Section>

      <OpenAccount open={openNew} onOpenChange={setOpenNew} />
      <PaymentSheet p={opened} onClose={() => setOpened(null)} />
    </div>
  );
}
