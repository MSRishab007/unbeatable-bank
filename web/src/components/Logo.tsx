import { motion } from "motion/react";
import { cn } from "@/lib/utils";

/**
 * The mark: a lowercase "un" drawn as one continuous stroke. The u's right stem carries
 * straight on to become the n's left stem, so the two letters share a spine and the n sits
 * half a step lower than the u, like a wave that keeps going.
 */
export const UN_PATH = "M14 16V34a9 9 0 0 0 18 0V30a9 9 0 0 1 18 0V48";
/** The bolt from the bank's first logo, kept as an accent for the headline's last line. */
export const BOLT_PATH = "M37 2 19 35h11l-5 27 20-36H33l4-24Z";

const TILE = {
  accent: "fill-accent text-accent-ink",
  dark: "fill-night text-sun",
  light: "fill-surface text-ink",
} as const;

export type MarkProps = {
  className?: string;
  /** tile: on a rounded square. bare: just the stroke, in the current text colour. */
  variant?: "tile" | "bare";
  /** Tile colours. accent: ink on orange. dark: yellow on night. light: ink on white. */
  tone?: keyof typeof TILE;
  /** Trims the canvas to the stroke, to set the mark inline with type. */
  crop?: boolean;
  /** Draws the stroke in when it first appears. */
  draw?: boolean;
  title?: string | null;
};

export function Mark({ className, variant = "tile", tone = "accent", crop = false, draw = false, title = "Unbeatable Bank" }: MarkProps) {
  const tile = variant === "tile";
  return (
    <svg
      viewBox={crop ? "8 10 48 44" : "0 0 64 64"}
      className={cn("shrink-0", tile && TILE[tone], className)}
      role={title ? "img" : undefined}
      aria-label={title ?? undefined}
      aria-hidden={title ? undefined : true}
    >
      {tile && <rect width="64" height="64" rx="18" />}
      <motion.path
        d={UN_PATH}
        fill="none"
        stroke="currentColor"
        strokeWidth={tile ? 7.5 : 11.5}
        strokeLinecap="round"
        strokeLinejoin="round"
        initial={draw ? { pathLength: 0 } : false}
        animate={{ pathLength: 1 }}
        transition={{ duration: 0.9, ease: [0.6, 0, 0.2, 1] }}
      />
    </svg>
  );
}

export function Bolt({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" aria-hidden className={cn("shrink-0 fill-current", className)}>
      <path d={BOLT_PATH} />
    </svg>
  );
}

/** The mark with the name. `short` is the app-bar form, "UNBank". */
export function Logo({ className, tone = "accent", size = "md", short = false, on = "light" }: {
  className?: string; tone?: MarkProps["tone"]; size?: "sm" | "md" | "lg"; short?: boolean;
  /** The surface the wordmark sits on. */
  on?: "light" | "dark" | "hero";
}) {
  const s = { sm: "size-8", md: "size-9", lg: "size-11" }[size];
  const t = { sm: "text-[15px]", md: "text-[17px]", lg: "text-xl" }[size];
  const ink = { light: "text-ink", dark: "text-on-night", hero: "text-hero-ink" }[on];
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <Mark className={s} tone={tone} title={null} />
      <span className={cn("whitespace-nowrap font-display font-semibold leading-none tracking-tight", t, ink)}>
        {short ? "UNBank" : "Unbeatable Bank"}
      </span>
    </span>
  );
}
