import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Copy, KeyRound, Lock, LogOut, Mail, Phone, ShieldAlert, Smartphone, Upload, FileCheck2, Monitor } from "lucide-react";
import { toast } from "sonner";
import { PageHead, Section } from "@/components/AppShell";
import { Avatar } from "@/components/Payment";
import { PinBoxes } from "@/components/Prompts";
import { KycNotice } from "./Home";
import { Badge, Card, Skeleton } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/input";
import { api, get } from "@/lib/api";
import { lockNow, signOut } from "@/lib/auth";
import { queryClient, useMe, usePins } from "@/lib/query";
import { askSetPin, PIN_LEN, type PinKind } from "@/lib/session";
import { copyText, inr, when, words } from "@/lib/utils";

const KYC_TYPES = ["image/jpeg", "image/png", "application/pdf"];

function ResetPin({ kind, onClose }: { kind: PinKind | null; onClose: () => void }) {
  const [sent, setSent] = useState(false);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { setSent(false); setCode(""); setPassword(""); setPin(""); }, [kind]);
  if (!kind) return null;
  const n = PIN_LEN[kind];
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} title={`Reset your ${kind === "PAYMENT" ? "payment" : "app"} PIN`}
      description={sent ? "Enter the code we just sent (it's in your Inbox too), your password, and the new PIN."
        : "Forgotten, or locked after three wrong tries? We'll send a code to your email and to your inbox."}>
      {!sent ? (
        <Button size="lg" className="w-full" disabled={busy} onClick={async () => {
          setBusy(true);
          try { await api("POST", `/api/auth/pins/${kind.toLowerCase()}/reset-code`, undefined, { raw: true }); setSent(true); queryClient.invalidateQueries({ queryKey: ["inbox"] }); }
          catch (e) { toast.error((e as Error).message); } finally { setBusy(false); }
        }}>Send me a code</Button>
      ) : (
        <form className="space-y-5" onSubmit={async (e) => {
          e.preventDefault(); setBusy(true);
          try {
            await api("POST", `/api/auth/pins/${kind.toLowerCase()}/reset`, { code, password, new_pin: pin }, { raw: true });
            toast.success("PIN reset. Payments to new payees get an extra check for a day.");
            queryClient.invalidateQueries({ queryKey: ["pins"] }); onClose();
          } catch (ex) { toast.error((ex as Error).message); } finally { setBusy(false); }
        }}>
          <Field label="6-digit code"><PinBoxes kind="OTP" mask={false} value={code} onChange={setCode} /></Field>
          <Field label="Your password" htmlFor="rp-pw"><Input id="rp-pw" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
          <Field label={`New ${n}-digit PIN`}><PinBoxes kind={kind} value={pin} onChange={setPin} autoFocus={false} /></Field>
          <Button type="submit" size="lg" className="w-full" disabled={busy || code.length !== 6 || pin.length !== n || !password}>Save new PIN</Button>
        </form>
      )}
    </Modal>
  );
}

function ChangeContact({ field, current, onClose }: { field: "email" | "phone" | null; current: string; onClose: () => void }) {
  const [value, setValue] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setValue(""); setPassword(""); setCode(""); setSent(false); }, [field]);
  if (!field) return null;
  const label = field === "email" ? "email" : "mobile number";
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} title={sent ? "Enter the code" : `Change your ${label}`}
      description={sent ? "It's in your current email and in your Inbox here." : "We'll send a code to your current email and to this app. For 3 days afterwards, payments to new payees may need an extra check."}>
      {!sent ? (
        <form className="space-y-5" onSubmit={async (e) => {
          e.preventDefault(); setBusy(true);
          try {
            await api("POST", "/api/auth/contact", { field, value: field === "phone" ? "+91" + value.trim() : value.trim().toLowerCase(), password });
            setSent(true); queryClient.invalidateQueries({ queryKey: ["inbox"] });
          } catch (ex) { toast.error((ex as Error).message); } finally { setBusy(false); }
        }}>
          <Field label={`New ${label}`} hint={`Now: ${current}`} htmlFor="cc-value">
            <Input id="cc-value" required type={field === "email" ? "email" : "text"} inputMode={field === "phone" ? "numeric" : undefined}
              pattern={field === "phone" ? "[6-9][0-9]{9}" : undefined} placeholder={field === "phone" ? "10 digits" : undefined} value={value} onChange={(e) => setValue(e.target.value)} />
          </Field>
          <Field label="Your password" htmlFor="cc-pw"><Input id="cc-pw" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
          <Button type="submit" size="lg" className="w-full" disabled={busy}>Send me a code</Button>
        </form>
      ) : (
        <form className="space-y-5" onSubmit={async (e) => {
          e.preventDefault(); setBusy(true);
          try {
            await api("POST", "/api/auth/contact/confirm", { code });
            toast.success(`Your ${label} is changed. We've told your old one too.`);
            queryClient.invalidateQueries({ queryKey: ["me"] }); onClose();
          } catch (ex) { toast.error((ex as Error).message); } finally { setBusy(false); }
        }}>
          <PinBoxes kind="OTP" mask={false} value={code} onChange={setCode} />
          <Button type="submit" size="lg" className="w-full" disabled={busy || code.length !== 6}>Change my {label}</Button>
        </form>
      )}
    </Modal>
  );
}

function ChangePassword({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Change your password" description="Every other device signed in to your account will be signed out.">
      <form className="space-y-5" onSubmit={async (e) => {
        e.preventDefault(); setBusy(true);
        try {
          const r = await api("POST", "/api/auth/password", { current_password: cur, new_password: next });
          toast.success(`Password changed. ${r.other_sessions_ended} other session${r.other_sessions_ended === 1 ? "" : "s"} signed out.`);
          onOpenChange(false); setCur(""); setNext("");
          queryClient.invalidateQueries({ queryKey: ["sessions"] });
        } catch (ex) { toast.error((ex as Error).message); } finally { setBusy(false); }
      }}>
        <Field label="Current password" htmlFor="cp-cur"><Input id="cp-cur" type="password" autoComplete="current-password" required value={cur} onChange={(e) => setCur(e.target.value)} /></Field>
        <Field label="New password" hint="At least 8 characters." htmlFor="cp-new"><Input id="cp-new" type="password" autoComplete="new-password" minLength={8} required value={next} onChange={(e) => setNext(e.target.value)} /></Field>
        <Button type="submit" size="lg" className="w-full" disabled={busy}>Change password</Button>
      </form>
    </Modal>
  );
}

function BlockEverything({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const [sure, setSure] = useState(false);
  const [busy, setBusy] = useState(false);
  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Block everything?"
      description="Every account is frozen at once, every device is signed out, including this one, and a fraud officer is told. Money can still come in. Unfreezing needs the bank to speak to you.">
      <label className="flex items-center gap-3 rounded-2xl bg-mist p-4 text-[15px] font-medium">
        <input type="checkbox" checked={sure} onChange={(e) => setSure(e.target.checked)} className="size-5 accent-[var(--danger)]" />
        I think someone else has got into my account
      </label>
      <Button variant="danger" size="lg" className="mt-5 w-full" disabled={!sure || busy} onClick={async () => {
        setBusy(true);
        try {
          const r = await api<{ status: number; data: any }>("POST", "/api/me/lockdown", undefined, { full: true, accept: [207] });
          const d = r.data;
          toast.success(`Blocked. ${d.accounts_frozen.length} account${d.accounts_frozen.length === 1 ? "" : "s"} frozen${d.case_number ? `, case ${d.case_number} opened` : ""}. Call the bank on 1800 000 0000.`);
          if (d.accounts_failed.length) toast.error(`We couldn't freeze ${d.accounts_failed.join(", ")}. Call the bank now.`);
          setTimeout(() => signOut(true), 600);
        } catch (e) { toast.error((e as Error).message); } finally { setBusy(false); }
      }}>Block everything</Button>
    </Modal>
  );
}

function Documents() {
  const q = useQuery({ queryKey: ["kyc"], queryFn: () => get("/api/kyc/documents") });
  const input = useRef<HTMLInputElement>(null);
  const [target, setTarget] = useState<string>("");
  if (!q.data) return <Skeleton className="h-40" />;
  return (
    <Card className="overflow-hidden" id="kyc-documents">
      <input ref={input} type="file" hidden accept=".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf" data-kyc-file={target}
        onChange={async (e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (!file) return;
          if (!KYC_TYPES.includes(file.type)) return toast.error("Choose a JPEG or PNG picture, or a PDF.");
          if (file.size > q.data.max_bytes) return toast.error(`That file is ${(file.size / 1048576).toFixed(1)} MB. The limit is ${q.data.max_bytes / 1048576} MB.`);
          try {
            await api("POST", `/api/kyc/documents?doc_type=${target}`, file);
            toast.success("Sent for review"); q.refetch(); queryClient.invalidateQueries({ queryKey: ["me"] });
          } catch (ex) { toast.error((ex as Error).message); }
        }} />
      <ul className="divide-y divide-line/70">
        {q.data.types.map((t: any) => {
          const doc = t.document;
          const label = t.label[0].toUpperCase() + t.label.slice(1);
          return (
            <li key={t.doc_type} data-kyc={t.doc_type} className="flex flex-wrap items-center gap-3 px-5 py-4">
              <FileCheck2 className="size-5 text-muted" />
              <div className="min-w-0 flex-1">
                <p className="font-semibold">{label}{t.required ? "" : <span className="font-normal text-muted"> (optional)</span>}</p>
                <p className="text-[13px] text-muted">{doc?.status === "REJECTED" ? <span className="text-danger">Not accepted: {doc.reject_reason}</span> : doc ? `Sent ${when(doc.uploaded_at)}` : "JPEG, PNG or PDF"}</p>
              </div>
              {doc ? <Badge tone={doc.status === "APPROVED" ? "good" : doc.status === "REJECTED" ? "bad" : "warn"}>{({ PENDING: "Being reviewed", APPROVED: "Accepted", REJECTED: "Not accepted" } as any)[doc.status]}</Badge>
                : t.required && <Badge tone="warn">Needed</Badge>}
              {doc?.status !== "APPROVED" && (
                <Button size="sm" variant={!doc || doc.status === "REJECTED" ? "primary" : "secondary"} onClick={() => { setTarget(t.doc_type); setTimeout(() => input.current?.click(), 0); }}>
                  <Upload /> {!doc ? "Upload" : doc.status === "REJECTED" ? "Upload again" : "Replace"}
                </Button>
              )}
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

export default function Profile() {
  const { hash } = useLocation();
  const me = useMe();
  const pins = usePins();
  const sec = useQuery({
    queryKey: ["sessions"],
    queryFn: async () => {
      const [devices, sessions, logins] = await Promise.all([get("/api/auth/devices"), get("/api/auth/sessions"), get("/api/auth/login-history")]);
      return { devices, sessions, logins };
    },
  });
  const [reset, setReset] = useState<PinKind | null>(null);
  const [contact, setContact] = useState<"email" | "phone" | null>(null);
  const [pw, setPw] = useState(false);
  const [block, setBlock] = useState(false);
  useEffect(() => { if (hash) setTimeout(() => document.getElementById(hash.slice(1))?.scrollIntoView({ behavior: "smooth" }), 300); }, [hash]);
  const m = me.data;
  const deviceName = Object.fromEntries((sec.data?.devices || []).map((d: any) => [d.device_id, d.label]));

  return (
    <div>
      <PageHead title="Profile" actions={<>
        <Button variant="secondary" size="sm" onClick={() => lockNow()}><Lock /> Lock now</Button>
        <Button variant="secondary" size="sm" onClick={() => signOut()}><LogOut /> Sign out</Button>
      </>} />
      <KycNotice me={m} />
      {m ? (
        <Card className="flex flex-wrap items-center gap-4 p-5 sm:p-6">
          <Avatar name={m.full_name} className="size-16 text-lg" />
          <div className="min-w-0 flex-1">
            <p className="font-display text-xl font-semibold tracking-tight">{m.full_name}</p>
            <p className="text-[13px] text-muted">Customer number <b className="text-ink-2 num">{m.cif_number}</b></p>
            <button type="button" className="mt-1 inline-flex items-center gap-1 text-[13px] font-semibold text-cobalt" onClick={async () => { if (await copyText(m.customer_id)) toast.success("Customer ID copied"); }}>
              Copy customer ID <Copy className="size-3.5" />
            </button>
          </div>
          <Badge tone={m.kyc_status === "VERIFIED" ? "good" : m.kyc_status === "REJECTED" ? "bad" : "warn"}>KYC {words(m.kyc_status).toLowerCase()}</Badge>
        </Card>
      ) : <Skeleton className="h-28" />}

      <Section title="PINs">
        <div id="pins" className="grid gap-3 sm:grid-cols-2">
          {(["PAYMENT", "APP"] as const).map((k) => {
            const st = pins.data?.[k];
            return (
              <Card key={k} data-pin={k} className="p-5">
                <div className="flex items-start justify-between gap-3">
                  <span className="flex size-11 items-center justify-center rounded-2xl bg-cobalt/8 text-cobalt"><KeyRound className="size-5" /></span>
                  {st && (!st.set ? <Badge tone="warn">Not set</Badge> : st.locked ? <Badge tone="bad">Locked until {when(st.locked_until)}</Badge> : <Badge tone="good">Set</Badge>)}
                </div>
                <p className="mt-4 font-semibold">{k === "PAYMENT" ? "Payment PIN" : "App PIN"} <span className="font-normal text-muted">· {PIN_LEN[k]} digits</span></p>
                <p className="text-[13px] text-muted">{k === "PAYMENT" ? "Approves money leaving your account." : "Unlocks the app and approves deposits."}</p>
                <div className="mt-4 flex gap-2">
                  {st && !st.set && <Button size="sm" onClick={() => askSetPin(k).then(() => pins.refetch(), () => {})}>Set</Button>}
                  {st?.set && !st.locked && <Button size="sm" variant="secondary" onClick={() => askSetPin(k, undefined, true).then(() => pins.refetch(), () => {})}>Change</Button>}
                  {st?.set && <Button size="sm" variant="ghost" onClick={() => setReset(k)}>{st.locked ? "Reset to unlock" : "Forgot"}</Button>}
                </div>
              </Card>
            );
          })}
        </div>
        <p className="mt-3 text-[13px] text-muted">Three wrong tries lock a PIN for 24 hours and we tell you. We never ask for your PIN by phone, email or message.</p>
      </Section>

      <Section title="Sign-in details">
        <Card className="overflow-hidden">
          <ul className="divide-y divide-line/70">
            {[
              { icon: Mail, k: "Email", v: m?.email, act: () => setContact("email") },
              { icon: Phone, k: "Mobile", v: m?.phone, act: () => setContact("phone") },
              { icon: KeyRound, k: "Password", v: "••••••••", act: () => setPw(true) },
            ].map(({ icon: Icon, k, v, act }) => (
              <li key={k} className="flex items-center gap-3.5 px-5 py-4">
                <Icon className="size-5 text-muted" />
                <div className="min-w-0 flex-1"><p className="text-[13px] text-muted">{k}</p><p className="truncate font-semibold num">{v}</p></div>
                <Button size="sm" variant="ghost" onClick={act}>Change</Button>
              </li>
            ))}
            {m && <li className="flex items-center justify-between px-5 py-4 text-[15px]"><span className="text-muted">Daily transfer limit</span><b className="num">{inr(m.daily_transfer_limit)}</b></li>}
          </ul>
        </Card>
      </Section>

      <Section title="Identity documents" className="scroll-mt-24">
        <div id="documents" className="scroll-mt-24"><Documents /></div>
        <p className="mt-3 text-[13px] text-muted">Only KYC officers can open these, and every time one does it is recorded.</p>
      </Section>

      <Section title="Signed in now">
        <Card className="overflow-hidden" id="sessions">
          {sec.data ? (
            <ul className="divide-y divide-line/70">
              {sec.data.sessions.map((x: any) => (
                <li key={x.session_id} className="flex items-center gap-3.5 px-5 py-4">
                  {/Android|iOS/.test(deviceName[x.device_id] || "") ? <Smartphone className="size-5 text-muted" /> : <Monitor className="size-5 text-muted" />}
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-semibold">{deviceName[x.device_id] || x.ip || "Unknown device"}</p>
                    <p className="truncate text-[13px] text-muted">Since {when(x.created_at)}{x.ip ? ` from ${x.ip}` : ""}</p>
                  </div>
                  {x.current ? <Badge tone="good">This device</Badge> : (
                    <Button size="sm" variant="danger-ghost" onClick={async () => {
                      try { await api("DELETE", `/api/auth/sessions/${x.session_id}`); toast.success("Signed out there"); sec.refetch(); } catch (e) { toast.error((e as Error).message); }
                    }}>Sign out</Button>
                  )}
                </li>
              ))}
            </ul>
          ) : <Skeleton className="h-24" />}
        </Card>
      </Section>

      <Section title="Recent sign-ins">
        <Card className="overflow-hidden">
          <ul className="divide-y divide-line/70">
            {(sec.data?.logins || []).slice(0, 8).map((l: any, i: number) => (
              <li key={i} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-5 py-3 text-[15px]">
                <span className="w-36 shrink-0 text-muted">{when(l.at)}</span>
                <Badge tone={/SUCCESS|OK/i.test(l.result) ? "good" : l.result === "CHALLENGED" ? "warn" : "bad"}>{words(l.result)}</Badge>
                <span className="text-muted num">{l.ip}</span>
                {l.new_device && <Badge tone="warn">New device</Badge>}
                {l.impossible_travel && <Badge tone="bad">Impossible travel</Badge>}
              </li>
            ))}
          </ul>
        </Card>
      </Section>

      <Section title="Something wrong?">
        <div className="flex flex-col gap-4 rounded-[var(--radius-card)] border border-danger/25 bg-bad-bg/50 p-5 sm:flex-row sm:items-center">
          <ShieldAlert className="size-8 shrink-0 text-danger" />
          <div className="flex-1">
            <p className="font-semibold">Block everything</p>
            <p className="text-[15px] text-ink-2">Lost your phone, or see a payment you didn't make? This freezes every account and signs out every device in one go.</p>
          </div>
          <Button variant="danger" id="block-everything" onClick={() => setBlock(true)}>Block everything</Button>
        </div>
      </Section>

      <ResetPin kind={reset} onClose={() => { setReset(null); pins.refetch(); }} />
      <ChangeContact field={contact} current={contact === "email" ? m?.email || "" : m?.phone || ""} onClose={() => setContact(null)} />
      <ChangePassword open={pw} onOpenChange={setPw} />
      <BlockEverything open={block} onOpenChange={setBlock} />
    </div>
  );
}
