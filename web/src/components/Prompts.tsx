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
import { haptic, spring } from "@/lib/motion";
import { api, ApiError } from "@/lib/api";
import { Cancelled, PIN_LEN, PIN_NAME, usePrompts, useSession, type PinKind } from "@/lib/session";
import { signOut } from "@/lib/auth";

const PIN_USE: Record<PinKind, string> = {
  PAYMENT: "Your 6-digit payment PIN approves money leaving your account.",
  APP: "Your 4-digit app PIN unlocks the app and approves deposits.",
};

/**
 * Segmented boxes for a PIN or a one-time code (the input-otp package: one real input, so
 * paste, auto-advance and SMS autofill work). Bump `shake` to shake it after a wrong entry.
 */
export function PinBoxes({ kind, value, onChange, onComplete, invalid, autoFocus = true, mask = true, id, name, shake = 0 }: {
  kind: PinKind | "OTP"; value: string; onChange: (v: string) => void; onComplete?: (v: string) => void;
  invalid?: boolean; autoFocus?: boolean; mask?: boolean; id?: string; name?: string; shake?: number;
}) {
  const n = kind === "OTP" ? 6 : PIN_LEN[kind];
  useEffect(() => {
    if (shake) haptic([20, 30, 20]);
  }, [shake]);
  return (
    <motion.div key={shake} animate={shake ? { x: [0, -12, 12, -8, 8, -4, 0] } : undefined} transition={{ duration: 0.4 }}>
      <InputOTP
        id={id}
        name={name}
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
        <PinBoxes kind={req.kind} value={value} onChange={setValue} onComplete={(v) => setTimeout(() => close(v), 160)} invalid={!!req.problem && !value} shake={shake} />
        {req.problem && <p role="alert" className="text-center text-sm font-medium text-danger">{req.problem}</p>}
        <Button type="submit" className="w-full" size="lg" disabled={value.length !== PIN_LEN[req.kind]}>Confirm</Button>
        <p className="text-center text-sm text-muted">
          Forgot it?{" "}
          <Link to="/profile" className="font-semibold text-accent-text underline underline-offset-2" onClick={() => close()}>Reset it in Profile</Link>
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
        {error && <p role="alert" className="text-sm font-medium text-danger">{error}</p>}
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
    <div role="dialog" aria-modal="true" aria-labelledby="lock-title" className="on-dark fixed inset-0 z-[60] flex items-center justify-center bg-night p-5 text-on-night">
      <motion.div initial={{ opacity: 0, y: 24, scale: 0.96 }} animate={{ opacity: 1, y: 0, scale: 1 }} transition={spring.page} className="w-full max-w-sm text-center">
        <span className="relative mx-auto flex size-20 items-center justify-center">
          <Mark className="size-20" tone="accent" draw />
          <span className="absolute -right-2 -bottom-2 flex size-8 items-center justify-center rounded-full bg-sun text-sun-ink"><Lock className="size-4" strokeWidth={2.5} /></span>
        </span>
        <h2 id="lock-title" className="mt-7 font-display text-2xl font-semibold tracking-tight">Welcome back{session?.name ? `, ${session.name.split(" ")[0]}` : ""}</h2>
        <p className="mt-2 text-on-night-muted">
          We locked the app after 5 minutes without use. {usePassword ? "Enter your password." : "Enter your app PIN."}
        </p>
        <form className="mt-8 space-y-4" onSubmit={(e) => { e.preventDefault(); if (value) unlock(value); }}>
          {usePassword ? (
            <>
              <Input ref={pw} type="password" autoComplete="current-password" autoFocus aria-label="Password" value={value}
                onChange={(e) => setValue(e.target.value)} placeholder="Password" />
              <Button type="submit" variant="sun" size="lg" className="w-full" disabled={busy || !value}>Unlock</Button>
            </>
          ) : (
            <PinBoxes kind="APP" value={value} onChange={setValue} onComplete={(v) => unlock(v)} invalid={!!problem && !value} shake={shake} />
          )}
          {problem && <p role="alert" className="text-sm font-semibold text-sun">{problem}</p>}
        </form>
        <div className="mt-8 flex justify-center gap-6 text-sm">
          <button type="button" className="press rounded-full font-semibold text-on-night underline underline-offset-4" onClick={() => { setUsePassword(!usePassword); setValue(""); setProblem(""); }}>
            {usePassword ? "Use my app PIN" : "Use my password instead"}
          </button>
          <button type="button" className="press rounded-full font-semibold text-on-night underline underline-offset-4" onClick={() => { usePrompts.setState({ lock: null }); req.reject(new Cancelled()); signOut(); }}>
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
