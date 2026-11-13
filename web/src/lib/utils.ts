import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const rupees = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const rupeesWhole = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const timeFmt = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
const dayFmt = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric" });

/** Paise to "₹1,234.50" (amounts are always paise in the API). */
export const inr = (paise: number | null | undefined, whole = false) => {
  const v = Math.abs(paise || 0) / 100;
  return ((paise || 0) < 0 ? "−₹" : "₹") + (whole ? rupeesWhole.format(v) : rupees.format(v));
};

/** "1,234.5" or "1234" typed by a person, to paise; null if it isn't a positive amount. */
export const toPaise = (text: string): number | null => {
  const clean = String(text || "").replace(/[,\s₹]/g, "");
  if (!/^\d+(\.\d{1,2})?$/.test(clean)) return null;
  const p = Math.round(parseFloat(clean) * 100);
  return p > 0 ? p : null;
};

const asDate = (v: string | number) => (typeof v === "number" ? new Date(v * 1000) : new Date(v));
export const when = (v?: string | number | null) => (v == null ? "" : timeFmt.format(asDate(v)));
export const day = (v?: string | null) =>
  v == null ? "" : dayFmt.format(new Date(String(v).length === 10 ? v + "T00:00:00" : v));
export const pct = (rate: number) => (rate * 100).toFixed(2).replace(/\.?0+$/, "") + "%";
export const words = (code?: string | null) =>
  String(code || "").toLowerCase().replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
export const groupDigits = (n: string | number) => String(n).replace(/(\d{4})(?=\d)/g, "$1 ");
export const initials = (name?: string | null) =>
  String(name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join("");
export const short = (id?: string) => String(id || "").slice(0, 8);

export function uuid(): string {
  if (crypto.randomUUID) return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6]! & 0x0f) | 0x40;
  b[8] = (b[8]! & 0x3f) | 0x80;
  const x = [...b].map((n) => n.toString(16).padStart(2, "0")).join("");
  return `${x.slice(0, 8)}-${x.slice(8, 12)}-${x.slice(12, 16)}-${x.slice(16, 20)}-${x.slice(20)}`;
}

/** Indian mobile numbers may be typed without +91; everything else is an email. */
export const normaliseIdentifier = (raw: string) => {
  const t = raw.trim();
  return /^[6-9]\d{9}$/.test(t) ? "+91" + t : t.toLowerCase();
};

export const reducedMotion = () =>
  typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;

export async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
