import { useState } from "react";
import { Link } from "react-router-dom";
import { motion } from "motion/react";
import { ArrowRight, Fingerprint, Landmark, QrCode, ShieldAlert, Radar, Check } from "lucide-react";
import { Logo } from "@/components/Logo";
import { WordRoller } from "@/components/WordRoller";
import { SwipeButton } from "@/components/SwipeButton";
import { Button } from "@/components/ui/button";

const CHECKS: [string, string][] = [
  ["Duplicate check", "One reference, one payment. Pressing Pay twice can't move money twice."],
  ["Rate limits", "Shared by every server, so a script can't outrun them."],
  ["Account and payee", "You see the name before a rupee moves."],
  ["Your limits", "Per payment, per day, and a careful first day for new payees."],
  ["Risk engine", "Scores device, place, payee and pattern in milliseconds."],
  ["Double-entry ledger", "Debits always equal credits, in one transaction."],
];

const FEATURES = [
  { icon: QrCode, title: "Pay anyone, three ways", text: "By account number, by pay ID like riya@unbeatable, or by scanning a QR code." },
  { icon: Landmark, title: "Deposits that show the maths", text: "Fixed and recurring deposits with a live quote before you commit, and early closure priced upfront." },
  { icon: Fingerprint, title: "Two PINs, one lock", text: "A 6-digit PIN for money, a 4-digit one to unlock. The app locks itself after five quiet minutes." },
  { icon: Radar, title: "Watched as it happens", text: "Stream analytics finds mule rings and probing that no single payment would show." },
];

export default function Landing() {
  const [settled, setSettled] = useState(false);
  return (
    <div className="min-h-dvh bg-mist">
      {/* ---------------------------------------------------------------- hero */}
      <section className="relative overflow-hidden bg-cobalt text-white">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 opacity-[0.16] [background-image:radial-gradient(rgba(255,255,255,.9)_1px,transparent_1px)] [background-size:22px_22px] [mask-image:linear-gradient(180deg,#000,transparent_85%)]"
        />
        <header className="relative mx-auto flex max-w-6xl items-center justify-between px-4 py-5 sm:px-6">
          <Logo tone="light" />
          <nav className="flex items-center gap-1.5 sm:gap-2">
            <Button asChild variant="ghost" size="sm" className="hidden text-white hover:bg-white/10 sm:inline-flex">
              <Link to="/signin">Sign in</Link>
            </Button>
            <Button asChild variant="light" size="sm">
              <Link to="/register">Open account</Link>
            </Button>
          </nav>
        </header>

        <div className="relative mx-auto grid max-w-6xl items-center gap-12 px-4 pt-10 pb-20 sm:px-6 lg:grid-cols-[1.25fr_1fr] lg:pt-16 lg:pb-28">
          <div>
            <WordRoller onSettled={() => setSettled(true)} />
            <motion.p
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.3, duration: 0.5 }}
              className="mt-8 max-w-[34rem] text-[17px] leading-relaxed text-white/75"
            >
              Savings, payments and deposits in one app. Every payment passes six checks before money moves,
              and a stream of analytics watches for the fraud no single payment reveals.
            </motion.p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Button asChild variant="volt" size="lg">
                <Link to="/register">Open an account <ArrowRight /></Link>
              </Button>
              <Button asChild variant="ghost" size="lg" className="border border-white/30 text-white hover:bg-white/10">
                <Link to="/signin">Sign in</Link>
              </Button>
            </div>
          </div>

          {/* A payment, as it looks in the app. The slider plays once the headline has landed. */}
          <motion.div
            initial={{ opacity: 0, y: 24, rotate: 2 }}
            animate={{ opacity: 1, y: 0, rotate: 0 }}
            transition={{ delay: 0.5, type: "spring", stiffness: 120, damping: 18 }}
            className="mx-auto w-full max-w-[380px] rounded-[30px] bg-white p-5 text-ink shadow-[0_40px_80px_-30px_rgba(10,8,90,.7)]"
          >
            <div className="flex items-center gap-3">
              <div className="flex size-11 items-center justify-center rounded-full bg-[#ffe7d6] font-semibold text-[#9a4a12]">RS</div>
              <div>
                <p className="text-[13px] text-muted">Paying</p>
                <p className="whitespace-nowrap font-semibold">Riya Sharma</p>
              </div>
              <span className="ml-auto inline-flex items-center gap-1 rounded-full bg-good-bg px-2.5 py-1 text-xs font-semibold text-go-deep">
                <Check className="size-3.5" strokeWidth={3} /> Verified
              </span>
            </div>
            <p className="mt-8 text-center font-display text-[44px] font-semibold tracking-tight num">₹1,250</p>
            <p className="mt-1 text-center text-sm text-muted">Dinner at Toit</p>
            <div className="mt-6 space-y-2 rounded-2xl bg-mist p-4 text-[13px]">
              {["Duplicate check", "Risk engine: score 4 of 100", "Ledger"].map((c) => (
                <div key={c} className="flex items-center gap-2">
                  <span className="flex size-4 items-center justify-center rounded-full bg-go text-white">
                    <Check className="size-2.5" strokeWidth={4} />
                  </span>
                  {c}
                </div>
              ))}
            </div>
            <SwipeButton className="mt-6" label="Swipe to pay ₹1,250" demo={settled} />
          </motion.div>
        </div>
      </section>

      {/* ---------------------------------------------------------------- six checks */}
      <section className="mx-auto max-w-6xl px-4 py-20 sm:px-6 lg:py-28">
        <div className="grid gap-12 lg:grid-cols-[1fr_1.2fr]">
          <div>
            <h2 className="font-display text-[clamp(1.9rem,4vw,2.9rem)] font-semibold leading-[1.05] tracking-[-0.03em]">
              Six checks before a rupee moves.
            </h2>
            <p className="mt-5 max-w-md text-[17px] leading-relaxed text-ink-2">
              They run on every payment, in order, and the app shows you each one. Load-tested at 130 requests a second:
              99.99% answered, 95% of them in under 1.4 seconds.
            </p>
          </div>
          <ol className="relative">
            {CHECKS.map(([name, text], i) => (
              <motion.li
                key={name}
                initial={{ opacity: 0, x: 12 }}
                whileInView={{ opacity: 1, x: 0 }}
                viewport={{ once: true, margin: "-60px" }}
                transition={{ delay: i * 0.12, duration: 0.4 }}
                className="relative flex gap-4 pb-7 last:pb-0"
              >
                {i < CHECKS.length - 1 && <span aria-hidden className="absolute top-8 left-[15px] h-[calc(100%-2rem)] w-0.5 bg-line" />}
                <motion.span
                  initial={{ scale: 0.6, backgroundColor: "#e3e5f0" }}
                  whileInView={{ scale: 1, backgroundColor: "#12a663" }}
                  viewport={{ once: true, margin: "-60px" }}
                  transition={{ delay: i * 0.12 + 0.2, type: "spring", stiffness: 300 }}
                  className="relative z-10 flex size-8 shrink-0 items-center justify-center rounded-full text-white"
                >
                  <Check className="size-4" strokeWidth={3} />
                </motion.span>
                <div className="pt-1">
                  <p className="font-semibold">{name}</p>
                  <p className="mt-0.5 text-[15px] text-muted">{text}</p>
                </div>
              </motion.li>
            ))}
          </ol>
        </div>
      </section>

      {/* ---------------------------------------------------------------- features */}
      <section className="border-y border-line bg-surface">
        <div className="mx-auto grid max-w-6xl gap-x-12 gap-y-10 px-4 py-16 sm:grid-cols-2 sm:px-6 lg:py-20">
          {FEATURES.map(({ icon: Icon, title, text }) => (
            <div key={title} className="flex gap-4">
              <span className="flex size-12 shrink-0 items-center justify-center rounded-2xl bg-cobalt/8 text-cobalt">
                <Icon className="size-6" />
              </span>
              <div>
                <h3 className="text-lg font-semibold tracking-tight">{title}</h3>
                <p className="mt-1 text-[15px] leading-relaxed text-muted">{text}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* ---------------------------------------------------------------- block everything */}
      <section className="mx-auto max-w-6xl px-4 py-16 sm:px-6 lg:py-20">
        <div className="flex flex-col items-start gap-6 rounded-[32px] bg-cobalt-ink p-8 text-white sm:p-12 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex gap-5">
            <ShieldAlert className="mt-1 size-9 shrink-0 text-volt" />
            <div>
              <h2 className="font-display text-2xl font-semibold tracking-tight sm:text-3xl">Lost your phone? One tap blocks everything.</h2>
              <p className="mt-3 max-w-xl text-white/70">
                Every account frozen, every device signed out, a fraud officer told. Money can still come in.
              </p>
            </div>
          </div>
          <Button asChild variant="volt" size="lg" className="shrink-0">
            <Link to="/register">Open an account</Link>
          </Button>
        </div>
      </section>

      <footer className="mx-auto flex max-w-6xl flex-col gap-6 px-4 pb-12 text-sm text-muted sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <Logo size="sm" />
        <nav className="flex flex-wrap gap-x-6 gap-y-2">
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
