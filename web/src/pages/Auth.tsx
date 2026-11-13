import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { motion } from "motion/react";
import { ArrowLeft, MailCheck } from "lucide-react";
import { toast } from "sonner";
import { Logo, Mark } from "@/components/Logo";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/dialog";
import { PinBoxes } from "@/components/Prompts";
import { api } from "@/lib/api";
import { finishSignIn, login, verifyDevice, type DeviceChallenge } from "@/lib/auth";
import { normaliseIdentifier } from "@/lib/utils";

function AuthFrame({ children, aside }: { children: React.ReactNode; aside: React.ReactNode }) {
  return (
    <div className="grid min-h-dvh lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
      <aside className="relative hidden overflow-hidden bg-cobalt p-12 text-white lg:flex lg:flex-col lg:justify-between">
        <div aria-hidden className="pointer-events-none absolute inset-0 opacity-[0.14] [background-image:radial-gradient(rgba(255,255,255,.9)_1px,transparent_1px)] [background-size:22px_22px]" />
        <Link to="/" className="relative"><Logo tone="light" /></Link>
        <div className="relative">{aside}</div>
        <Mark tile={false} title={null} crop className="pointer-events-none absolute -right-16 -bottom-24 h-[420px] w-auto opacity-[0.12]" />
      </aside>
      <main className="flex flex-col px-5 py-6 sm:px-10 lg:justify-center lg:px-16">
        <div className="mb-10 flex items-center justify-between lg:hidden">
          <Link to="/"><Logo /></Link>
        </div>
        <div className="mx-auto w-full max-w-[420px]">{children}</div>
      </main>
    </div>
  );
}

function ForgotPassword({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const [stage, setStage] = useState<0 | 1>(0);
  const [identifier, setIdentifier] = useState("");
  const [code, setCode] = useState("");
  const [dob, setDob] = useState("");
  const [pw, setPw] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setError("");
    try { await fn(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <Modal open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) { setStage(0); setCode(""); setError(""); } }}
      title={stage === 0 ? "Reset your password" : "Choose a new password"}
      description={stage === 0 ? "We'll email a code to the address you registered with. You'll also need your date of birth."
        : "If that's your login, the code is in your email now. Every device you're signed in on will be signed out."}>
      {stage === 0 ? (
        <form className="space-y-5" onSubmit={(e) => { e.preventDefault(); run(async () => {
          await api("POST", "/api/auth/password/forgot", { identifier: normaliseIdentifier(identifier) }, { anonymous: true });
          setStage(1);
        }); }}>
          <Field label="Email or mobile number" htmlFor="fp-id">
            <Input id="fp-id" autoComplete="username" required value={identifier} onChange={(e) => setIdentifier(e.target.value)} autoFocus />
          </Field>
          {error && <p role="alert" className="text-sm text-danger">{error}</p>}
          <Button type="submit" size="lg" className="w-full" disabled={busy}>Send me a code</Button>
        </form>
      ) : (
        <form className="space-y-5" onSubmit={(e) => { e.preventDefault(); run(async () => {
          await api("POST", "/api/auth/password/reset", { identifier: normaliseIdentifier(identifier), code, date_of_birth: dob, new_password: pw }, { anonymous: true });
          toast.success("Password changed. Sign in with the new one.");
          onOpenChange(false); setStage(0);
        }); }}>
          <Field label="6-digit code"><PinBoxes kind="OTP" mask={false} value={code} onChange={setCode} /></Field>
          <Field label="Date of birth" htmlFor="fp-dob"><Input id="fp-dob" type="date" required value={dob} onChange={(e) => setDob(e.target.value)} /></Field>
          <Field label="New password" hint="At least 8 characters." htmlFor="fp-pw">
            <Input id="fp-pw" type="password" autoComplete="new-password" minLength={8} required value={pw} onChange={(e) => setPw(e.target.value)} />
          </Field>
          {error && <p role="alert" className="text-sm text-danger">{error}</p>}
          <Button type="submit" size="lg" className="w-full" disabled={busy || code.length !== 6}>Save new password</Button>
        </form>
      )}
    </Modal>
  );
}

function DeviceCode({ challenge, identifier, onBack, onDone }: {
  challenge: DeviceChallenge; identifier: string; onBack: () => void; onDone: () => void;
}) {
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [forgot, setForgot] = useState(false);
  async function submit(value = code) {
    if (busy || value.length !== 6) return;
    setBusy(true); setError("");
    try {
      const t = await verifyDevice(challenge.challenge_id, value);
      await finishSignIn(t, identifier);
      onDone();
    } catch (e) {
      setError((e as Error).message); setCode("");
    } finally { setBusy(false); }
  }
  return (
    <motion.div initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }}>
      <button type="button" onClick={onBack} className="mb-8 inline-flex items-center gap-1.5 text-sm font-semibold text-muted hover:text-ink">
        <ArrowLeft className="size-4" /> Back
      </button>
      <span className="flex size-14 items-center justify-center rounded-2xl bg-cobalt/10 text-cobalt"><MailCheck className="size-7" /></span>
      <h1 className="mt-6 font-display text-[28px] font-semibold tracking-tight">Is this you?</h1>
      <p className="mt-2 text-[15px] leading-relaxed text-muted">
        This is the first time you've signed in on this device. {challenge.message || "We sent you a code."} It expires in 5 minutes.
      </p>
      <form id="device-code" className="mt-8 space-y-5" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <PinBoxes kind="OTP" mask={false} value={code} onChange={setCode} onComplete={(v) => submit(v)} invalid={!!error} />
        {error && <p role="alert" className="text-center text-sm text-danger">{error}</p>}
        <Button type="submit" size="lg" className="w-full" disabled={busy || code.length !== 6}>Confirm and sign in</Button>
      </form>
      <p className="mt-8 text-sm text-muted">
        Didn't ask for this? Someone has your password:{" "}
        <button type="button" className="font-semibold text-cobalt" onClick={() => setForgot(true)}>reset it now</button>.
      </p>
      <ForgotPassword open={forgot} onOpenChange={setForgot} />
    </motion.div>
  );
}

export function SignIn() {
  const nav = useNavigate();
  const [params] = useSearchParams();
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [forgot, setForgot] = useState(false);
  const [challenge, setChallenge] = useState<{ c: DeviceChallenge; id: string } | null>(null);
  const done = () => nav(params.get("next") || "/home", { replace: true });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    const id = normaliseIdentifier(identifier);
    try {
      const r = await login(id, password);
      if (r.challenge) setChallenge({ c: r.challenge, id });
      else { await finishSignIn(r.tokens!, id); done(); }
    } catch (ex) { setError((ex as Error).message); } finally { setBusy(false); }
  }

  return (
    <AuthFrame aside={<><p className="font-display text-4xl font-semibold leading-tight tracking-tight">Good to see you again.</p>
      <p className="mt-4 max-w-sm text-white/70">New device? We'll send a code to the phone and email you registered, before anything else.</p></>}>
        {challenge ? (
          <DeviceCode key="code" challenge={challenge.c} identifier={challenge.id} onBack={() => setChallenge(null)} onDone={done} />
        ) : (
          <motion.div key="form" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
            <h1 className="font-display text-[32px] font-semibold tracking-tight">Sign in</h1>
            <p className="mt-2 text-muted">Use the email or mobile number you registered with.</p>
            <form className="mt-8 space-y-5" onSubmit={submit}>
              <Field label="Email or mobile number" htmlFor="identifier">
                <Input id="identifier" name="identifier" autoComplete="username" required value={identifier} onChange={(e) => setIdentifier(e.target.value)} autoFocus />
              </Field>
              <Field label={<span className="flex justify-between">Password<button type="button" className="font-semibold text-cobalt" onClick={() => setForgot(true)}>Forgot?</button></span>} htmlFor="password">
                <Input id="password" name="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
              </Field>
              {error && <p role="alert" className="text-sm text-danger">{error}</p>}
              <Button type="submit" size="lg" className="w-full" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</Button>
            </form>
            <p className="mt-8 text-center text-[15px] text-muted">
              New here? <Link to="/register" className="font-semibold text-cobalt">Open an account</Link>
            </p>
            <p className="mt-3 text-center text-sm text-muted">
              Bank staff? <a href="/classic/#/staff" className="font-semibold text-ink-2 hover:text-ink">Staff console</a>
            </p>
          </motion.div>
        )}
      <ForgotPassword open={forgot} onOpenChange={setForgot} />
    </AuthFrame>
  );
}

export function Register() {
  const nav = useNavigate();
  const [v, setV] = useState({ full_name: "", date_of_birth: "", phone: "", email: "", pan: "", password: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [challenge, setChallenge] = useState<{ c: DeviceChallenge; id: string } | null>(null);
  const set = (k: keyof typeof v) => (e: React.ChangeEvent<HTMLInputElement>) => setV({ ...v, [k]: e.target.value });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    const body: Record<string, string> = { full_name: v.full_name.trim(), date_of_birth: v.date_of_birth, phone: "+91" + v.phone.trim(),
      email: v.email.trim().toLowerCase(), password: v.password };
    if (v.pan.trim()) body.pan = v.pan.trim().toUpperCase();
    try {
      await api("POST", "/api/auth/register", body, { anonymous: true });
      const r = await login(body.email!, body.password!);
      if (r.challenge) setChallenge({ c: r.challenge, id: body.email! });
      else { await finishSignIn(r.tokens!, body.email!); nav("/home?welcome=1", { replace: true }); }
    } catch (ex) { setError((ex as Error).message); } finally { setBusy(false); }
  }

  return (
    <AuthFrame aside={<><p className="font-display text-4xl font-semibold leading-tight tracking-tight">Two minutes to sign up. Six checks on every payment after that.</p>
      <p className="mt-4 max-w-sm text-white/70">A branch officer verifies your KYC before money can move. You can open accounts straight away.</p></>}>
      {challenge ? (
        <DeviceCode challenge={challenge.c} identifier={challenge.id} onBack={() => setChallenge(null)} onDone={() => nav("/home", { replace: true })} />
      ) : (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
          <h1 className="font-display text-[32px] font-semibold tracking-tight">Open your account</h1>
          <p className="mt-2 text-muted">Create a login first. It takes about two minutes.</p>
          <form className="mt-8 space-y-5" onSubmit={submit}>
            <Field label="Full name" htmlFor="full_name">
              <Input id="full_name" name="full_name" autoComplete="name" required minLength={2} value={v.full_name} onChange={set("full_name")} autoFocus />
            </Field>
            <div className="grid gap-5 sm:grid-cols-2">
              <Field label="Date of birth" htmlFor="dob">
                <Input id="dob" name="date_of_birth" type="date" required max={new Date().toISOString().slice(0, 10)} value={v.date_of_birth} onChange={set("date_of_birth")} />
              </Field>
              <Field label="Mobile number" htmlFor="phone">
                <div className="relative">
                  <span className="pointer-events-none absolute top-1/2 left-4 -translate-y-1/2 text-[15px] text-muted">+91</span>
                  <Input id="phone" name="phone" inputMode="numeric" autoComplete="tel-national" required pattern="[6-9][0-9]{9}"
                    title="10-digit Indian mobile number" placeholder="10 digits" className="pl-12" value={v.phone} onChange={set("phone")} />
                </div>
              </Field>
            </div>
            <Field label="Email" htmlFor="email">
              <Input id="email" name="email" type="email" autoComplete="email" required value={v.email} onChange={set("email")} />
            </Field>
            <Field label={<>PAN <span className="font-normal text-muted">(optional)</span></>} hint="We keep only a one-way hash and the last five characters." htmlFor="pan">
              <Input id="pan" name="pan" pattern="[A-Za-z]{5}[0-9]{4}[A-Za-z]" placeholder="ABCDE1234F" autoComplete="off" title="5 letters, 4 digits, 1 letter" value={v.pan} onChange={set("pan")} />
            </Field>
            <Field label="Password" hint="At least 8 characters." htmlFor="new-password">
              <Input id="new-password" name="password" type="password" autoComplete="new-password" required minLength={8} value={v.password} onChange={set("password")} />
            </Field>
            {error && <p role="alert" className="text-sm text-danger">{error}</p>}
            <Button type="submit" size="lg" className="w-full" disabled={busy}>{busy ? "Creating…" : "Create my login"}</Button>
          </form>
          <p className="mt-8 text-center text-[15px] text-muted">
            Already with us? <Link to="/signin" className="font-semibold text-cobalt">Sign in</Link>
          </p>
        </motion.div>
      )}
    </AuthFrame>
  );
}
