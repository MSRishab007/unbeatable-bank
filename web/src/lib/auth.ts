import { api, setOnSignedOut } from "./api";
import { deviceFingerprint, deviceLabel, isLocked, showLockScreen, useSession } from "./session";
import { queryClient } from "./query";
import { toast } from "sonner";

export type Tokens = {
  access_token: string;
  refresh_token?: string;
  idle_lock_seconds?: number;
  new_device?: boolean;
};
export type DeviceChallenge = { challenge_id: string; message?: string };

/** Password check. A device the bank hasn't seen gets a 202 and a code sent to the customer. */
export async function login(identifier: string, password: string): Promise<{ tokens?: Tokens; challenge?: DeviceChallenge }> {
  const r = await api<{ status: number; data: any }>("POST", "/api/auth/login",
    { identifier, password, device_fingerprint: deviceFingerprint(), device_label: deviceLabel() },
    { anonymous: true, full: true });
  return r.status === 202 ? { challenge: r.data } : { tokens: r.data };
}

export async function verifyDevice(challengeId: string, code: string): Promise<Tokens> {
  return api("POST", "/api/auth/login/verify", { challenge_id: challengeId, code }, { anonymous: true });
}

export async function finishSignIn(t: Tokens, identifier: string) {
  const { set, patch } = useSession.getState();
  set({ kind: "customer", access: t.access_token, refresh: t.refresh_token, name: "Customer", email: identifier,
    idleLock: t.idle_lock_seconds || 300 });
  try {
    const me = await api("GET", "/api/me");
    patch({ name: me.full_name, email: me.email });
    queryClient.setQueryData(["me"], me);
  } catch {
    /* loads again on Home */
  }
  if (t.new_device) toast.success("New device confirmed. We've told you by email and in your inbox.");
}

export function signOut(expired = false) {
  const s = useSession.getState().session;
  if (s && !expired) {
    fetch(s.kind === "customer" ? "/api/auth/logout" : "/api/staff/logout", {
      method: "POST", headers: { Authorization: "Bearer " + s.access },
    }).catch(() => {});
  }
  useSession.getState().set(null);
  queryClient.clear();
  if (expired) toast.error("Your session ended. Sign in again.");
  if (!/^#\/(signin|register)?$/.test(location.hash)) location.hash = expired ? "/signin" : "/";
}
setOnSignedOut((expired) => signOut(expired));

// ---------------------------------------------------------------- idle lock

/** The server locks an idle session itself; the app also hides the screen after the same time. */
const idle = { last: Date.now(), timer: 0 as number | undefined };
["pointerdown", "keydown", "wheel", "touchstart"].forEach((ev) =>
  addEventListener(ev, () => {
    if (!isLocked()) idle.last = Date.now();
  }, { passive: true, capture: true }));

export async function lockNow() {
  try {
    await api("POST", "/api/auth/lock", undefined, { raw: true });
  } catch {
    /* the server locks it anyway */
  }
  if (useSession.getState().session) await showLockScreen().catch(() => {});
  idle.last = Date.now();
}

export function startIdleWatch() {
  if (idle.timer) return () => {};
  idle.last = Date.now();
  idle.timer = window.setInterval(() => {
    const s = useSession.getState().session;
    if (!s || s.kind !== "customer" || isLocked()) return;
    if (Date.now() - idle.last > (s.idleLock || 300) * 1000) lockNow();
  }, 5000);
  return () => {
    clearInterval(idle.timer);
    idle.timer = undefined;
  };
}
