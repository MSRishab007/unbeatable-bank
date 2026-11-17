import * as React from "react";
import { cn } from "@/lib/utils";

/** A plain white panel. Lists and forms sit on these; hero panels use their own colour. */
export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-card bg-surface", className)} {...props} />;
}

const TONES = {
  neutral: "bg-sunken text-ink-2",
  good: "bg-success-soft text-success-text",
  warn: "bg-warn-soft text-warn",
  bad: "bg-danger-soft text-danger",
  accent: "bg-accent-soft text-accent-text",
} as const;
export type Tone = keyof typeof TONES;

export function Badge({ tone = "neutral", className, ...props }: React.HTMLAttributes<HTMLSpanElement> & { tone?: Tone }) {
  return (
    <span
      className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-semibold", TONES[tone], className)}
      {...props}
    />
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-field bg-ink/7", className)} />;
}

/** A coloured notice with a title: KYC waiting, account frozen, and so on. */
export function Notice({ tone, icon, title, children, className }: {
  tone: "warn" | "bad" | "good"; icon?: React.ReactNode; title: React.ReactNode; children?: React.ReactNode; className?: string;
}) {
  const t = { warn: "bg-warn-soft [&>svg]:text-warn", bad: "bg-danger-soft [&>svg]:text-danger", good: "bg-success-soft [&>svg]:text-success-text" }[tone];
  return (
    <div className={cn("flex gap-3 rounded-card p-4 text-[15px] sm:p-5 [&>svg]:mt-0.5 [&>svg]:size-5 [&>svg]:shrink-0", t, className)}>
      {icon}
      <div>
        <p className="font-semibold">{title}</p>
        {children && <div className="mt-0.5 text-ink-2">{children}</div>}
      </div>
    </div>
  );
}
