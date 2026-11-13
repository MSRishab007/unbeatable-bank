import * as React from "react";
import { cn } from "@/lib/utils";

export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-[var(--radius-card)] bg-surface border border-line/70", className)} {...props} />;
}

export function Badge({ tone = "neutral", className, ...props }: React.HTMLAttributes<HTMLSpanElement> & {
  tone?: "neutral" | "good" | "warn" | "bad" | "cobalt";
}) {
  const tones = {
    neutral: "bg-mist text-ink-2",
    good: "bg-good-bg text-go-deep",
    warn: "bg-warn-bg text-[#8a5d00]",
    bad: "bg-bad-bg text-danger",
    cobalt: "bg-cobalt/10 text-cobalt",
  } as const;
  return (
    <span
      className={cn("inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold whitespace-nowrap", tones[tone], className)}
      {...props}
    />
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-xl bg-ink/[0.06]", className)} />;
}
