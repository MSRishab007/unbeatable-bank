import { cn } from "@/lib/utils";
import type { Account } from "@/lib/types";

/**
 * The colour of an account, shared by its card on Home and its header on the account page
 * (the card grows into that header, so both must be the same surface).
 * Savings is yellow, current is night, a frozen account goes grey.
 */
export function accountTone(a: Pick<Account, "product_type" | "status">) {
  if (a.status === "FROZEN") return "bg-sunken text-ink-2";
  return a.product_type === "CURRENT" ? "on-dark bg-night text-on-night" : "bg-sun text-sun-ink";
}

export const accountLayoutId = (id: string) => `account-${id}`;

export const STATUS_LABEL: Record<string, string> = { PENDING_KYC: "Waiting for KYC", FROZEN: "Frozen", CLOSED: "Closed" };

export function StatusPill({ status, className }: { status: string; className?: string }) {
  if (status === "ACTIVE") return null;
  return <span className={cn("rounded-full bg-current/15 px-2.5 py-0.5 text-xs font-semibold", className)}><span className="text-current">{STATUS_LABEL[status] || status}</span></span>;
}
