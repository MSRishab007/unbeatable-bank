import { useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import { Link } from "react-router-dom";
import { Lock } from "lucide-react";
import { toast } from "sonner";
import { Modal } from "./ui/dialog";
import { InputOTP, InputOTPGroup, InputOTPSlot } from "./ui/input-otp";
import { Button } from "./ui/button";
import { Field, Input } from "./ui/input";
import { Mark } from "./Logo";
import { api, ApiError } from "@/lib/api";
import { Cancelled, PIN_LEN, PIN_NAME, usePrompts, useSession, type PinKind } from "@/lib/session";
import { signOut } from "@/lib/auth";

const PIN_USE: Record<PinKind, string> = {
  PAYMENT: "Your 6-digit payment PIN approves money leaving your account.",
  APP: "Your 4-digit app PIN unlocks the app and approves deposits.",
};

export function PinBoxes({ kind, value, onChange, onComplete, invalid, autoFocus = true, mask = true, id }: {
  kind: PinKind | "OTP"; value: string; onChange: (v: string) => void; onComplete?: (v: string) => void;
  invalid?: boolean; autoFocus?: boolean; mask?: boolean; id?: string;
}) {
  const n = kind === "OTP" ? 6 : PIN_LEN[kind];
  return (
    <InputOTP
      id={id}
      maxLength={n}
      value={value}
      onChange={onChange}
      onComplete={onComplete}
      autoFocus={autoFocus}
      inputMode="numeric"
      pattern="^[0-9]*$"
      autoComplete={kind === "OTP" ? "one-time-code" : "off"}
      aria-label={kind === "OTP" ? "6-digit code" : PIN_NAME[kind]}
      containerClassName="justify-center"
    >
      <InputOTPGroup>
        {Array.from({ length: n }, (_, i) => <InputOTPSlot key={i} index={i} mask={mask} invalid={invalid} />)}
      </InputOTPGroup>
    </InputOTP>
  );
}

function Shake({ on, children }: { on: number; children: React.ReactNode }) {
  return (
    <motion.div key={on} animate={on ? { x: [0, -10, 10, -6, 6, 0] } : {}} transition={{ duration: 0.35 }}>
      {children}
    </motion.div>
  );
}

/** Raised by the API client when the bank asks for a PIN (428 PIN_REQUIRED, or a wrong one). */
function PinSheet() {
  const req = usePrompts((s) => s.pin);
  const [value, setValue] = useState("");
  const [shake, setShake] = useState(0);
  useEffect(() => {
    setValue("");
    if (req?.problem) setShake((n) => n + 1);
  }, [req]);
  if (!req) return null;
  const close = (pin?: string) => {
    usePrompts.setState({ pin: null });
    if (pin) req.resolve(pin);
    else req.reject(new Cancelled());
  };
  return (
    <Modal
      open
      onOpenChange={(o) => !o && close()}
      title={`Enter your ${PIN_NAME[req.kind]}`}
      description={req.context || PIN_USE[req.kind]}
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (value.length === PIN_LEN[req.kind]) close(value);
        }}
        className="space-y-5"
      >
        <Shake on={shake}>
          <PinBoxes kind={req.kind} value={value} onChange={setValue} onComplete={(v) => setTimeout(() => close(v), 120)} invalid={!!req.problem && !value} />
        </Shake>
        {req.problem && <p role="alert" className="text-center text-sm text-danger">{req.problem}</p>}
        <Button type="submit" className="w-full" size="lg" disabled={value.length !== PIN_LEN[req.kind]}>Confirm</Button>
        <p className="text-center text-sm text-muted">
          Forgot it?{" "}
          <Link to="/profile" className="font-semibold text-cobalt" onClick={() => close()}>Reset it in Profile</Link>
        </p>
      </form>
    </Modal>
  );
}

/** First time: the password. Changing: the current PIN. */
function SetPinSheet() {
  const req = usePrompts((s) => s.setPin);
  const [secret, setSecret] = useState("");
  const [pin, setPin] = useState("");
  const [again, setAgain] = useState("");
  const [stage, setStage] = useState<0 | 1>(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setSecret(""); setPin(""); setAgain(""); setStage(0); setError("");
  }, [req]);
  if (!req) return null;
  const n = PIN_LEN[req.kind];
  const close = (ok: boolean) => {
    usePrompts.setState({ setPin: null });
    if (ok) req.resolve();
    else req.reject(new Cancelled());
  };
  async function save() {
    if (pin !== again) {
      setError("The two PINs don't match. Try again.");
      setAgain(""); setStage(0); setPin("");
      return;
    }
    setBusy(true);
    try {
      const body = req!.change ? { pin, current_pin: secret } : { pin, password: secret };
      await api("PUT", `/api/auth/pins/${req!.kind.toLowerCase()}`, body, { raw: true });
      toast.success(`Your ${PIN_NAME[req!.kind]} is ${req!.change ? "changed" : "set"}`);
      close(true);
    } catch (e) {
      setError((e as Error).message);
      setAgain(""); setPin(""); setStage(0);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal open onOpenChange={(o) => !o && close(false)} title={`${req.change ? "Change" : "Set"} your ${PIN_NAME[req.kind]}`} description={req.lead || PIN_USE[req.kind]}>
      <form onSubmit={(e) => { e.preventDefault(); if (stage === 1 && again.length === n) save(); }} className="space-y-5">
        {req.change ? (
          <Field label={`Current ${n}-digit PIN`}>
            <PinBoxes kind={req.kind} value={secret} onChange={setSecret} autoFocus id="current-pin" />
          </Field>
        ) : (
          <Field label="Your password" htmlFor="pin-password">
            <Input id="pin-password" type="password" autoComplete="current-password" value={secret} onChange={(e) => setSecret(e.target.value)} autoFocus />
          </Field>
        )}
        {stage === 0 ? (
          <Field label={`New ${n}-digit PIN`}>
            <PinBoxes kind={req.kind} value={pin} onChange={setPin} autoFocus={false} onComplete={() => setTimeout(() => setStage(1), 150)} />
          </Field>
        ) : (
          <Field label="Type it again">
            <PinBoxes kind={req.kind} value={again} onChange={setAgain} autoFocus />
          </Field>
        )}
        <p className="text-[13px] text-muted">
          {req.kind === "APP" ? "It can't be the first or last four digits of your payment PIN. Avoid 1234 or 1111."
            : "Your app PIN can't be its first or last four digits. Avoid 123456 or 111111."}
        </p>
        {error && <p role="alert" className="text-sm text-danger">{error}</p>}
        <Button type="submit" size="lg" className="w-full" disabled={busy || !secret || stage !== 1 || again.length !== n}>Save PIN</Button>
      </form>
    </Modal>
  );
}

/** Above everything, can't be dismissed. Every request that met a locked session waits on it. */
function LockScreen() {
  const req = usePrompts((s) => s.lock);
  const session = useSession((s) => s.session);
  const [usePassword, setUsePassword] = useState(false);
  const [value, setValue] = useState("");
  const [problem, setProblem] = useState("");
  const [shake, setShake] = useState(0);
  const [busy, setBusy] = useState(false);
  const pw = useRef<HTMLInputElement>(null);
  useEffect(() => {
    setValue(""); setProblem(""); setUsePassword(false);
  }, [req]);
  if (!req) return null;

  async function unlock(secret: string) {
    if (busy) return;
    setBusy(true);
    setProblem("");
    try {
      const r = await api<{ status: number; data: any }>("POST", "/api/auth/unlock", usePassword ? { password: secret } : { pin: secret },
        { raw: true, full: true, accept: [403, 428] });
      if (r.status === 428) {
        setUsePassword(true);
        setValue("");
        setProblem("You haven't set an app PIN yet. Use your password this time.");
        return;
      }
      if (r.status === 403) {
        const left = r.data?.details?.attempts_left;
        setProblem(left != null ? `That's not right. ${left} ${left === 1 ? "try" : "tries"} left.` : "That's not right.");
        setValue("");
        setShake((n) => n + 1);
        return;
      }
      usePrompts.setState({ lock: null });
      req!.resolve();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return;
      setProblem((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="lock-title" className="fixed inset-0 z-[60] flex items-center justify-center bg-cobalt p-5 text-white">
      <div aria-hidden className="pointer-events-none absolute inset-0 opacity-[0.14] [background-image:radial-gradient(rgba(255,255,255,.9)_1px,transparent_1px)] [background-size:22px_22px]" />
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} className="relative w-full max-w-sm text-center">
        <Mark className="mx-auto size-16" bg="#fff" u="var(--cobalt)" />
        <h2 id="lock-title" className="mt-6 font-display text-2xl font-semibold">Welcome back{session?.name ? `, ${session.name.split(" ")[0]}` : ""}</h2>
        <p className="mt-2 text-white/70">
          <Lock className="mr-1 inline size-4 -translate-y-px" />
          We locked the app after 5 minutes without use. {usePassword ? "Enter your password." : "Enter your app PIN."}
        </p>
        <form className="mt-8 space-y-4" onSubmit={(e) => { e.preventDefault(); if (value) unlock(value); }}>
          {usePassword ? (
            <>
              <Input ref={pw} type="password" autoComplete="current-password" autoFocus aria-label="Password" value={value}
                onChange={(e) => setValue(e.target.value)} className="border-white/20 bg-white/10 text-white placeholder:text-white/50 focus:border-white focus:ring-white/20" placeholder="Password" />
              <Button type="submit" variant="volt" size="lg" className="w-full" disabled={busy || !value}>Unlock</Button>
            </>
          ) : (
            <Shake on={shake}>
              <PinBoxes kind="APP" value={value} onChange={setValue} onComplete={(v) => unlock(v)} invalid={!!problem} />
            </Shake>
          )}
          {problem && <p role="alert" className="text-sm font-medium text-volt">{problem}</p>}
        </form>
        <div className="mt-8 flex justify-center gap-6 text-sm">
          <button type="button" className="font-semibold text-white/85 hover:text-white" onClick={() => { setUsePassword(!usePassword); setValue(""); setProblem(""); }}>
            {usePassword ? "Use my app PIN" : "Use my password instead"}
          </button>
          <button type="button" className="font-semibold text-white/85 hover:text-white" onClick={() => { usePrompts.setState({ lock: null }); req.reject(new Cancelled()); signOut(); }}>
            Sign out
          </button>
        </div>
      </motion.div>
    </div>
  );
}

export function Prompts() {
  return (
    <>
      <PinSheet />
      <SetPinSheet />
      <LockScreen />
    </>
  );
}
