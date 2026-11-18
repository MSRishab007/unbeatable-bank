/** What a scanned or pasted code asks for. */
export type PayCode = { to: string; amount: string; note: string };

/**
 * Reads an Unbeatable Bank payment code. Three forms are accepted:
 *   a pay link    https://<host>/pay?to=<12-digit account>&amount=<rupees>&note=<text>
 *                 (older codes: https://<host>/#/pay?to=...)
 *   an account    12 digits, spaces allowed
 *   a pay ID      name@unbeatable
 * Anything else is not ours and returns null. (Same rules as parsePayCode in the classic app.)
 */
export function parsePayCode(text: string | null | undefined): PayCode | null {
  const t = String(text || "").trim();
  const m = t.match(/\/pay\?(.*)$/);
  if (m) {
    const q = new URLSearchParams(m[1]);
    const to = (q.get("to") || "").trim();
    if (to) return { to, amount: q.get("amount") || "", note: q.get("note") || "" };
  }
  const digits = t.replace(/\s/g, "");
  if (/^\d{12}$/.test(digits)) return { to: digits, amount: "", note: "" };
  if (/^[\w.\-]+@unbeatable$/i.test(t)) return { to: t.toLowerCase(), amount: "", note: "" };
  return null;
}
