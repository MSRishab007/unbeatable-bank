import { useState } from "react";
import { Link } from "react-router-dom";
import { motion } from "motion/react";
import { BadgeCheck, Check, Fingerprint, Landmark, QrCode, Radar, ShieldAlert } from "lucide-react";
import { Logo } from "@/components/Logo";
import { Headline } from "@/components/Headline";
import { SwipeButton } from "@/components/SwipeButton";
import { Button } from "@/components/ui/button";
import { spring } from "@/lib/motion";

const CHECKS: [string, string][] = [
  ["Duplicate check", "One reference, one payment. Swiping twice can't move money twice."],
  ["Rate limits", "Shared by every server, so a script can't outrun them."],
  ["Account and payee", "You see the name before a rupee moves."],
  ["Your limits", "Per payment, per day, and a careful first day for new payees."],
  ["Risk engine", "Scores device, place, payee and pattern in milliseconds."],
  ["Double-entry ledger", "Debits always equal credits, in one transaction."],
];

const FEATURES = [
  { icon: QrCode, title: "Pay anyone, three ways", text: "By account number, by a pay ID like riya@unbeatable, or by scanning a QR code." },
  { icon: Landmark, title: "Deposits that show the maths", text: "Fixed and recurring deposits with a live quote before you commit, and early closure priced upfront." },
  { icon: Fingerprint, title: "Two PINs, one lock", text: "A 6-digit PIN for money, a 4-digit one to unlock. The app locks itself after five quiet minutes." },
  { icon: Radar, title: "Watched as it happens", text: "Stream analytics finds mule rings and probing that no single payment would show." },
];

/** A payment as it looks in the app. The swipe plays once the headline has landed. */
function PayDemo({ play }: { play: boolean }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 40, rotate: 4 }}
      animate={{ opacity: 1, y: 0, rotate: 2 }}
      transition={{ ...spring.page, delay: 0.35 }}
      className="mx-auto w-full max-w-[380px] rounded-panel bg-surface p-5 text-ink shadow-float"
    >
      <div className="flex items-center gap-3">
        <span className="flex size-11 items-center justify-center rounded-full bg-sun text-sm font-bold text-sun-ink">RS</span>
        <div>
          <p className="text-[13px] text-muted">Paying</p>
          <p className="whitespace-nowrap font-semibold">Riya Sharma</p>
        </div>
        <span className="ml-auto inline-flex items-center gap-1 rounded-full bg-success-soft px-2.5 py-1 text-xs font-semibold text-success-text">
          <BadgeCheck className="size-3.5" /> Name checked
        </span>
      </div>
      <p className="mt-7 text-center font-display text-[44px] font-semibold tracking-tight num">₹1,250</p>
      <p className="mt-1 text-center text-sm text-muted">Dinner at Toit</p>
      <ul className="mt-6 space-y-2 rounded-card bg-bg p-4 text-[13px]">
        {["Duplicate check", "Risk engine, score 4 of 100", "Ledger"].map((c) => (
          <li key={c} className="flex items-center gap-2">
            <span className="flex size-4 items-center justify-center rounded-full bg-success text-success-ink"><Check className="size-3" strokeWidth={3.5} /></span>
            {c}
          </li>
        ))}
      </ul>
      <SwipeButton className="mt-6" label="Swipe to pay ₹1,250" demo={play} />
    </motion.div>
  );
}

export default function Landing() {
  const [settled, setSettled] = useState(false);
  return (
    <div className="min-h-dvh bg-bg">
      {/* ---------------------------------------------------------------- hero */}
      <section className="relative overflow-hidden bg-hero text-hero-ink">
        <header className="mx-auto flex max-w-[1320px] items-center justify-between px-gutter py-5 sm:px-8">
          <Logo tone="dark" on="hero" className="max-sm:hidden" />
          <Logo tone="dark" on="hero" short className="sm:hidden" />
          <nav aria-label="Account" className="flex items-center gap-1.5 sm:gap-2">
            <Button asChild variant="ghost" size="sm" className="hidden text-hero-ink hover:bg-hero-ink/10 hover:text-hero-ink sm:inline-flex">
              <Link to="/signin">Sign in</Link>
            </Button>
            <Button asChild variant="night" size="sm"><Link to="/register">Open an account</Link></Button>
          </nav>
        </header>

        <div className="mx-auto grid max-w-[1320px] items-center gap-x-10 gap-y-14 px-gutter pt-8 pb-16 sm:px-8 lg:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)] lg:pt-14 lg:pb-24">
          <div className="min-w-0">
            <Headline onSettled={() => setSettled(true)} />
            <p className="mt-14 max-w-[36rem] text-[17px] leading-relaxed sm:text-lg">
              Savings, payments and deposits in one app. Every payment passes six checks before money moves, and
              stream analytics watches for the fraud no single payment reveals.
            </p>
            <div className="mt-7 flex flex-wrap gap-3">
              <Button asChild variant="night" size="lg"><Link to="/register">Open an account</Link></Button>
              <Button asChild variant="surface" size="lg"><Link to="/signin">Sign in</Link></Button>
            </div>
          </div>
          <PayDemo play={settled} />
        </div>
      </section>

      {/* ---------------------------------------------------------------- six checks */}
      <section className="mx-auto max-w-[1320px] px-gutter py-20 sm:px-8 lg:py-28">
        <div className="grid gap-12 lg:grid-cols-[1fr_1.2fr]">
          <div>
            <h2 className="font-display text-[clamp(1.9rem,4vw,3rem)] font-semibold leading-[1.05] tracking-[-0.035em]">
              Six checks before a rupee moves.
            </h2>
            <p className="mt-5 max-w-md text-[17px] leading-relaxed text-ink-2">
              They run on every payment, in order, and the app shows you each one. Load-tested at 130 requests a second:
              99.99% answered, 95% of them in under 1.4 seconds.
            </p>
          </div>
          <ol className="relative">
            {CHECKS.map(([name, text], i) => (
              <li key={name} className="relative flex gap-4 pb-7 last:pb-0">
                {i < CHECKS.length - 1 && <span aria-hidden className="absolute top-8 left-[15px] h-[calc(100%-2rem)] w-0.5 bg-line" />}
                <span className="relative z-10 flex size-8 shrink-0 items-center justify-center rounded-full bg-line">
                  <motion.span
                    className="absolute inset-0 flex items-center justify-center rounded-full bg-success text-success-ink"
                    initial={{ scale: 0 }}
                    whileInView={{ scale: 1 }}
                    viewport={{ once: true, margin: "-80px" }}
                    transition={{ ...spring.bouncy, delay: i * 0.13 }}
                  >
                    <Check className="size-4" strokeWidth={3} />
                  </motion.span>
                </span>
                <div className="pt-1">
                  <p className="font-semibold">{name}</p>
                  <p className="mt-0.5 text-[15px] text-muted">{text}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* ---------------------------------------------------------------- features */}
      <section className="bg-surface">
        <div className="mx-auto grid max-w-[1320px] gap-x-14 gap-y-10 px-gutter py-16 sm:grid-cols-2 sm:px-8 lg:py-20">
          {FEATURES.map(({ icon: Icon, title, text }) => (
            <div key={title} className="flex gap-4">
              <Icon className="mt-0.5 size-7 shrink-0 text-accent-text" strokeWidth={1.75} />
              <div>
                <h3 className="text-lg font-semibold tracking-tight">{title}</h3>
                <p className="mt-1 max-w-md text-[15px] leading-relaxed text-muted">{text}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* ---------------------------------------------------------------- block everything */}
      <section className="mx-auto max-w-[1320px] px-gutter py-16 sm:px-8 lg:py-20">
        <div className="on-dark flex flex-col items-start gap-6 rounded-panel bg-night p-8 text-on-night sm:p-12 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex gap-5">
            <ShieldAlert className="mt-1 size-9 shrink-0 text-sun" />
            <div>
              <h2 className="font-display text-2xl font-semibold tracking-tight sm:text-3xl">Lost your phone? One tap blocks everything.</h2>
              <p className="mt-3 max-w-xl text-on-night-muted">
                Every account frozen, every device signed out, a fraud officer told. Money can still come in.
              </p>
            </div>
          </div>
          <Button asChild variant="sun" size="lg" className="shrink-0"><Link to="/register">Open an account</Link></Button>
        </div>
      </section>

      <footer className="mx-auto flex max-w-[1320px] flex-col gap-6 px-gutter pb-12 text-sm text-muted sm:flex-row sm:items-center sm:justify-between sm:px-8">
        <Logo size="sm" />
        <nav aria-label="More" className="flex flex-wrap gap-x-6 gap-y-2">
          <a className="hover:text-ink" href="/classic/#/staff">Staff console</a>
          <a className="hover:text-ink" href="/classic/">Verify a statement</a>
          <a className="hover:text-ink" href="/api/docs">API</a>
          <a className="hover:text-ink" href="/classic/">Classic app</a>
        </nav>
        <p>A Cloud Computing project, BITS Pilani.</p>
      </footer>
    </div>
  );
}
