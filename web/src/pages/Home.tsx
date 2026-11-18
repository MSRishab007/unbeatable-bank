import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { motion, useMotionValue, useSpring, useTransform } from "motion/react";
import { BadgeAlert, Eye, EyeOff, PiggyBank, Plus, QrCode, ScanLine, Users } from "lucide-react";
import { toast } from "sonner";
import { Mark } from "@/components/Logo";
import { accountLayoutId, accountTone, StatusPill } from "@/components/AccountCard";
import { Empty, Section, sectionLink } from "@/components/AppShell";
import { PaymentRow, PaymentSheet } from "@/components/Payment";
import { Card, Notice, Skeleton } from "@/components/ui/card";
import { CountUp } from "@/components/ui/count-up";
import { Modal } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { spring } from "@/lib/motion";
import { queryClient, useAccounts, useMe, usePayments } from "@/lib/query";
import { payable, PRODUCT, type Account, type Me, type Payment } from "@/lib/types";
import { cn, groupDigits, reducedMotion } from "@/lib/utils";

const hour = new Date().getHours();
const GREETING = hour < 5 ? "Up late" : hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
const HIDDEN = "₹ ••••••";

export function KycNotice({ me }: { me?: Me }) {
  if (!me || me.kyc_status === "VERIFIED") return null;
  return (
    <Notice tone="warn" className="mb-6" icon={<BadgeAlert />}
      title={me.kyc_status === "REJECTED" ? "Your KYC was not accepted" : "Your KYC is waiting for the branch"}>
      You can open accounts now; they become active once your documents are accepted.{" "}
      <Link to="/profile#documents" className="font-semibold text-accent-text underline underline-offset-2">Upload your documents</Link>, or quote customer number{" "}
      <b className="num">{me.cif_number}</b> at a branch.
    </Notice>
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
              className={cn("press rounded-card border-2 p-4 text-left", product === v ? "border-ink bg-bg" : "border-line hover:border-ink/30")}>
              <span className={cn("mb-3 block h-2 w-10 rounded-full", v === "SAVINGS" ? "bg-sun" : "bg-night")} aria-hidden />
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

/** One account. Tapping it grows the card into the account page (same layoutId there). */
/**
 * A card you can almost pick up: it tilts toward the pointer (or finger), and a soft light
 * slides across it as it turns, like a real card under a lamp. Still under reduced motion.
 */
function useTilt() {
  const px = useMotionValue(0.5), py = useMotionValue(0.5);
  const sx = useSpring(px, { stiffness: 220, damping: 20 }), sy = useSpring(py, { stiffness: 220, damping: 20 });
  const rotateY = useTransform(sx, [0, 1], [-11, 11]);
  const rotateX = useTransform(sy, [0, 1], [9, -9]);
  const light = useTransform([sx, sy], ([x, y]) =>
    `radial-gradient(260px circle at ${(x as number) * 100}% ${(y as number) * 100}%, rgb(255 255 255 / 0.22), transparent 60%)`);
  const still = reducedMotion();
  const move = (e: React.PointerEvent<HTMLElement>) => {
    if (still) return;
    const r = e.currentTarget.getBoundingClientRect();
    px.set((e.clientX - r.left) / r.width); py.set((e.clientY - r.top) / r.height);
  };
  const leave = () => { px.set(0.5); py.set(0.5); };
  return { style: still ? {} : { rotateX, rotateY, transformPerspective: 900 }, light: still ? undefined : light, move, leave };
}

function AccountCard({ a, hidden }: { a: Account; hidden: boolean }) {
  const onHold = a.available_balance != null && a.ledger_balance != null ? a.ledger_balance - a.available_balance : 0;
  const tilt = useTilt();
  return (
    <motion.div layoutId={accountLayoutId(a.account_id)} transition={spring.shared} whileHover={{ y: -4 }} whileTap={{ scale: 0.97 }}
      style={tilt.style} onPointerMove={tilt.move} onPointerLeave={tilt.leave}
      className={cn("relative w-[84%] shrink-0 snap-start overflow-hidden rounded-panel sm:w-[330px] lg:w-auto", accountTone(a))}>
      {tilt.light && <motion.span aria-hidden className="pointer-events-none absolute inset-0 z-10" style={{ backgroundImage: tilt.light }} />}
      <Link to={`/accounts/${a.account_id}`} className="cover relative flex h-[196px] flex-col p-6">
        <Mark variant="bare" title={null} className="pointer-events-none absolute -right-12 -bottom-14 size-[210px] opacity-[0.08]" />
        <div className="relative flex items-center justify-between text-sm font-semibold">
          <span>{PRODUCT[a.product_type]}{a.my_role === "JOINT" ? ", joint" : ""}</span>
          <StatusPill status={a.status} />
        </div>
        <p className="balance relative mt-auto font-display text-[30px] font-semibold tracking-[-0.03em] num sm:text-[32px]">
          {a.available_balance == null ? "—" : hidden ? HIDDEN : <CountUp paise={a.available_balance} />}
        </p>
        <div className="relative mt-1.5 flex items-center justify-between text-[13px] font-medium">
          <span className="acct tracking-wider num">{groupDigits(a.account_number)}</span>
          <span>{onHold > 0 && !hidden ? <><CountUp paise={onHold} /> on hold</> : "Available"}</span>
        </div>
      </Link>
    </motion.div>
  );
}

const ACTIONS = [
  { to: "/pay?scan=1", label: "Scan", icon: ScanLine },
  { to: "/receive", label: "Receive", icon: QrCode },
  { to: "/pay#payees", label: "Payees", icon: Users },
  { to: "/deposits", label: "Deposit", icon: PiggyBank },
];

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

  return (
    <div className="items-start gap-10 lg:grid lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
      {/* ------------------------------------------------ money */}
      <div className="min-w-0">
        <div className="mb-5 flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-[15px] font-medium text-muted">{GREETING}{first ? `, ${first}` : ""}</h1>
            {accounts.isLoading ? <Skeleton className="mt-2 h-11 w-56" /> : (
              <p className="mt-1 font-display text-[34px] font-semibold leading-none tracking-[-0.04em] num sm:text-[44px]">
                {hidden ? HIDDEN : <CountUp paise={total} />}
              </p>
            )}
            <p className="mt-2 text-sm text-muted">
              {everyday.length > 1 ? `Available across ${everyday.length} accounts` : everyday.length === 1 ? "Available to spend" : "Open an account to get started"}
            </p>
          </div>
          <button type="button" onClick={() => setHidden(!hidden)} aria-pressed={hidden}
            className="press inline-flex shrink-0 items-center gap-1.5 rounded-full bg-surface px-3 py-2 text-sm font-semibold text-ink-2 hover:text-ink">
            {hidden ? <Eye className="size-4" /> : <EyeOff className="size-4" />} {hidden ? "Show" : "Hide"}<span className="sr-only"> balances</span>
          </button>
        </div>

        <KycNotice me={me.data} />

        {accounts.isLoading ? <Skeleton className="h-[196px] w-[84%] rounded-panel sm:w-[330px]" /> : (
          <div className="-mx-gutter flex snap-x snap-mandatory scroll-px-4 gap-3 overflow-x-auto px-gutter pt-1 pb-2 no-scrollbar sm:-mx-6 sm:scroll-px-6 sm:px-6 lg:mx-0 lg:grid lg:grid-cols-2 lg:gap-4 lg:overflow-visible lg:px-0">
            {everyday.map((a) => <AccountCard key={a.account_id} a={a} hidden={hidden} />)}
            <button type="button" onClick={() => setOpenNew(true)}
              className="cover add press flex h-[196px] w-[56%] shrink-0 snap-start flex-col items-center justify-center gap-2 rounded-panel border-2 border-dashed border-ink/20 text-ink-2 hover:border-ink hover:text-ink sm:w-[200px] lg:w-auto">
              <Plus className="size-7" />
              <span className="px-4 font-semibold">{everyday.length ? "Open another account" : "Open your first account"}</span>
            </button>
          </div>
        )}
      </div>

      {/* ------------------------------------------------ actions and recent payments */}
      <div className="min-w-0">
        <nav aria-label="Quick actions" className="mt-6 grid grid-cols-4 rounded-card bg-surface p-1.5 lg:mt-0">
          {ACTIONS.map(({ to, label, icon: Icon }) => (
            <Link key={label} to={to} className="press flex flex-col items-center gap-1.5 rounded-[calc(var(--radius-card)-6px)] py-3.5 text-[13px] font-semibold text-ink-2 hover:bg-bg hover:text-ink">
              <Icon className="size-6 text-accent-text" strokeWidth={1.9} />
              {label}
            </Link>
          ))}
        </nav>

        <Section title="Recent payments" className="mt-7" action={payments.data?.length ? <Link to="/activity" className={sectionLink}>See all</Link> : null}>
          {payments.isLoading ? <Skeleton className="h-48 rounded-card" /> : payments.data?.length ? (
            <Card className="overflow-hidden"><ul className="divide-y divide-line">{payments.data.map((p) => <PaymentRow key={p.txn_id} p={p} onOpen={setOpened} />)}</ul></Card>
          ) : (
            <Empty title="No payments yet" text="Money you send shows up here, with the checks it went through." action={<Button asChild variant="secondary"><Link to="/pay">Make a payment</Link></Button>} />
          )}
        </Section>
      </div>

      <OpenAccount open={openNew} onOpenChange={setOpenNew} />
      <PaymentSheet p={opened} onClose={() => setOpened(null)} />
    </div>
  );
}
