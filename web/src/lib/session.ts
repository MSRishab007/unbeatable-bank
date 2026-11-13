import { create } from "zustand";
import { uuid } from "./utils";

/** Same storage keys and shape as the classic UI, so both stay signed in together. */
export type Session = {
  kind: "customer" | "staff";
  access: string;
  refresh?: string;
  name: string;
  email?: string;
  idleLock?: number;
  role?: string;
};

const KEY = "ub.session";
let memory: Session | null = null;

function load(): Session | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Session) : memory;
  } catch {
    return memory;
  }
}

function save(value: Session | null) {
  memory = value;
  try {
    if (value) sessionStorage.setItem(KEY, JSON.stringify(value));
    else sessionStorage.removeItem(KEY);
  } catch {
    /* private mode: the in-memory copy is enough */
  }
}

type SessionState = {
  session: Session | null;
  set: (s: Session | null) => void;
  patch: (p: Partial<Session>) => void;
};

export const useSession = create<SessionState>((setState, get) => ({
  session: load(),
  set: (s) => {
    save(s);
    setState({ session: s });
  },
  patch: (p) => {
    const s = get().session;
    if (!s) return;
    const next = { ...s, ...p };
    save(next);
    setState({ session: next });
  },
}));

/** A stable id for this browser, so the bank can tell a known device from a new one. */
export function deviceFingerprint(): string {
  try {
    let id = localStorage.getItem("ub.device");
    if (!id) {
      id = "web-" + uuid();
      localStorage.setItem("ub.device", id);
    }
    return id;
  } catch {
    return (deviceFingerprint as unknown as { memo?: string }).memo ||= "web-" + uuid();
  }
}

export function deviceLabel(): string {
  const ua = navigator.userAgent;
  const browser = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox"
    : /Safari\//.test(ua) ? "Safari" : "Browser";
  const os = /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iOS" : /Windows/.test(ua) ? "Windows"
    : /Mac OS/.test(ua) ? "macOS" : /Linux/.test(ua) ? "Linux" : "device";
  return `${browser} on ${os}`;
}

// ---------------------------------------------------------------- prompts the API client raises

export type PinKind = "PAYMENT" | "APP";
export const PIN_LEN: Record<PinKind, number> = { PAYMENT: 6, APP: 4 };
export const PIN_NAME: Record<PinKind, string> = { PAYMENT: "payment PIN", APP: "app PIN" };

type Pending<T> = { resolve: (v: T) => void; reject: (e: Error) => void };

type PromptState = {
  pin: (Pending<string> & { kind: PinKind; problem?: string; context?: string }) | null;
  setPin: (Pending<void> & { kind: PinKind; lead?: string; change?: boolean }) | null;
  lock: Pending<void> | null;
};

export const usePrompts = create<PromptState>(() => ({ pin: null, setPin: null, lock: null }));

export class Cancelled extends Error {
  constructor() {
    super("Cancelled. Nothing was done.");
  }
}

/** What the PIN sheet shows under the title, e.g. "Pay ₹500 to Riya". Set by the screen asking. */
let pinContext: string | undefined;
export const withPinContext = async <T,>(context: string, fn: () => Promise<T>): Promise<T> => {
  pinContext = context;
  try {
    return await fn();
  } finally {
    pinContext = undefined;
  }
};

export function askPin(kind: PinKind, problem?: string): Promise<string> {
  return new Promise((resolve, reject) =>
    usePrompts.setState({ pin: { kind, problem, context: pinContext, resolve, reject } }));
}

export function askSetPin(kind: PinKind, lead?: string, change = false): Promise<void> {
  return new Promise((resolve, reject) => usePrompts.setState({ setPin: { kind, lead, change, resolve, reject } }));
}

let unlocking: Promise<void> | null = null;
export function showLockScreen(): Promise<void> {
  unlocking ||= new Promise<void>((resolve, reject) => usePrompts.setState({ lock: { resolve, reject } }))
    .finally(() => {
      unlocking = null;
    });
  return unlocking;
}
export const isLocked = () => unlocking !== null;
