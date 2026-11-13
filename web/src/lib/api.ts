import { askPin, askSetPin, PIN_NAME, showLockScreen, useSession, type PinKind } from "./session";
import { words } from "./utils";

export class ApiError extends Error {
  constructor(public status: number, message: string, public data?: unknown) {
    super(message);
  }
}

type ErrorBody = { error?: string; message?: string; detail?: unknown; details?: Record<string, unknown> };

export function messageOf(data: unknown, status: number): string {
  if (data && typeof data === "object") {
    const d = data as ErrorBody;
    if (typeof d.message === "string") return d.message;
    if (typeof d.detail === "string") return d.detail;
    if (Array.isArray(d.detail)) {
      return d.detail
        .map((x: { loc?: unknown[]; msg?: string }) => `${words(String(x.loc?.slice(-1)[0] ?? "input"))}: ${x.msg}`)
        .join(". ");
    }
  }
  if (status === 429) return "Too many attempts. Wait a minute and try again.";
  if (status === 403) return "You don't have access to that.";
  if (status === 404) return "Not found.";
  if (status >= 500) return "The bank couldn't finish that just now. Nothing was changed; try again.";
  return `Request failed (${status}).`;
}

let refreshing: Promise<boolean> | null = null;
function refreshSession(): Promise<boolean> {
  refreshing ||= (async () => {
    const s = useSession.getState().session;
    if (!s?.refresh) return false;
    try {
      const res = await fetch("/api/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: s.refresh }),
      });
      if (!res.ok) return false;
      const data = await res.json();
      useSession.getState().patch({ access: data.access_token, refresh: data.refresh_token || s.refresh });
      return true;
    } catch {
      return false;
    } finally {
      setTimeout(() => (refreshing = null), 0);
    }
  })();
  return refreshing;
}

export type ApiOptions = {
  headers?: Record<string, string>;
  /** Hand these statuses back instead of throwing. */
  accept?: number[];
  /** Return { status, data } instead of data. */
  full?: boolean;
  /** No token, and no lock or PIN handling: sign-in screens. */
  anonymous?: boolean;
  /** The app checking something on its own: doesn't keep an idle session awake or bring up the lock screen. */
  background?: boolean;
  /** The caller handles every answer itself (no lock screen or PIN prompts). */
  raw?: boolean;
  pin?: string;
  blob?: boolean;
  retried?: boolean;
};

export let onSignedOut: (expired: boolean) => void = () => {};
export const setOnSignedOut = (fn: (expired: boolean) => void) => (onSignedOut = fn);

/**
 * Every call to the bank goes through here. Two answers are handled for every screen:
 *   423 SESSION_LOCKED  the lock screen comes up; once unlocked the request is sent again
 *   428 PIN_REQUIRED    the PIN sheet comes up; the request is sent again with the PIN
 */
export async function api<T = any>(method: string, path: string, body?: unknown, opt: ApiOptions = {}): Promise<T> {
  const s = useSession.getState().session;
  const headers: Record<string, string> = { ...(opt.headers || {}) };
  const file = body instanceof Blob;
  if (file) headers["Content-Type"] = (body as Blob).type || "application/octet-stream";
  else if (body !== undefined) headers["Content-Type"] = "application/json";
  if (s && !opt.anonymous) headers.Authorization = "Bearer " + s.access;
  if (opt.background) headers["X-Background"] = "1";
  if (opt.pin) headers["X-Pin"] = opt.pin;

  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers,
      body: file ? (body as Blob) : body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "Can't reach the bank. Check your connection and try again.");
  }

  if (res.status === 401 && s && !opt.anonymous) {
    if (!opt.retried && s.kind === "customer" && s.refresh && (await refreshSession())) {
      return api(method, path, body, { ...opt, retried: true });
    }
    onSignedOut(true);
    throw new ApiError(401, "Your session ended. Sign in again.");
  }
  if (opt.blob && res.ok) return (await res.blob()) as T;

  const text = await res.text();
  let data: any = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  const code = data && typeof data === "object" ? (data as ErrorBody).error : undefined;
  const kind = (data?.details?.kind || "PAYMENT") as PinKind;

  if (!opt.raw && !opt.anonymous) {
    if (res.status === 423 && code === "SESSION_LOCKED" && s) {
      if (opt.background) throw new ApiError(423, "Locked");
      await showLockScreen();
      return api(method, path, body, opt);
    }
    if (res.status === 428 && code === "PIN_REQUIRED") {
      const pin = await askPin(kind);
      return api(method, path, body, { ...opt, pin });
    }
    if (res.status === 403 && code === "PIN_WRONG") {
      const pin = await askPin(kind, data.message);
      return api(method, path, body, { ...opt, pin });
    }
    if (res.status === 423 && code === "PIN_LOCKED") {
      throw new ApiError(423, data.message || `Your ${PIN_NAME[kind]} is locked. Reset it on the Profile page.`, data);
    }
    if (res.status === 428 && code === "PIN_NOT_SET") {
      await askSetPin(kind, `You need a ${PIN_NAME[kind]} for this. Set it now.`);
      return api(method, path, body, { ...opt, pin: undefined });
    }
  }
  if (!res.ok && !(opt.accept || []).includes(res.status)) throw new ApiError(res.status, messageOf(data, res.status), data);
  return (opt.full ? { status: res.status, data } : data) as T;
}

export const get = <T = any>(path: string) => api<T>("GET", path);
