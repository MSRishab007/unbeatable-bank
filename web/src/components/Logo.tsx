import { useId } from "react";
import { cn } from "@/lib/utils";

/**
 * The mark: a U split by a bolt. The U is drawn as one stroke; the bolt cuts a gap through
 * its base (a mask) and sits in that gap, so the letter reads as struck rather than decorated.
 */
export function Mark({ className, tile = true, crop = false, u = "#fff", bolt = "var(--volt)", bg = "var(--cobalt)", title = "Unbeatable Bank" }: {
  className?: string; tile?: boolean; u?: string; bolt?: string; bg?: string; title?: string | null; crop?: boolean;
}) {
  const id = useId().replace(/:/g, "");
  const BOLT = "M37.5 3.5 L22.5 33.5 H31.5 L26 60.5 L45.5 26.5 H35.5 L42 3.5 Z";
  return (
    <svg viewBox={crop ? "12 2 40 60" : "0 0 64 64"} className={cn("shrink-0", className)} role={title ? "img" : undefined} aria-label={title ?? undefined} aria-hidden={title ? undefined : true}>
      {tile && <rect width="64" height="64" rx="17" fill={bg} />}
      <defs>
        <mask id={`cut-${id}`} maskUnits="userSpaceOnUse">
          <rect width="64" height="64" fill="#fff" />
          <path d={BOLT} fill="#000" stroke="#000" strokeWidth="7" strokeLinejoin="round" />
        </mask>
      </defs>
      <path
        d="M17 13 V35 a15 15 0 0 0 30 0 V13"
        fill="none"
        stroke={u}
        strokeWidth="8.5"
        strokeLinecap="round"
        mask={`url(#cut-${id})`}
      />
      <path d={BOLT} fill={bolt} />
    </svg>
  );
}

export function Logo({ className, tone = "dark", size = "md" }: {
  className?: string; tone?: "dark" | "light"; size?: "sm" | "md" | "lg";
}) {
  const s = { sm: "size-7", md: "size-9", lg: "size-11" }[size];
  const t = { sm: "text-[15px]", md: "text-[17px]", lg: "text-xl" }[size];
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <Mark className={s} title={null} bg={tone === "light" ? "#fff" : "var(--cobalt)"} u={tone === "light" ? "var(--cobalt)" : "#fff"} />
      <span className={cn("whitespace-nowrap font-display font-semibold tracking-tight leading-none", t, tone === "light" ? "text-white" : "text-ink")}>
        Unbeatable<span className={tone === "light" ? "text-white/60" : "text-muted"}> Bank</span>
      </span>
    </span>
  );
}
