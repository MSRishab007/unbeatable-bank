
/**
 * The app's springs. CSS transitions use the tokens in index.css (--ease-spring, --dur-*);
 * anything driven by Motion uses these, so the whole app moves with the same weight.
 */
export const spring = {
  /** Taps, toggles, pills sliding between tabs. */
  snappy: { type: "spring" as const, stiffness: 520, damping: 38, mass: 0.9 },
  /** Pages, cards and sheets arriving. */
  page: { type: "spring" as const, stiffness: 300, damping: 30, mass: 0.9 },
  /** Things that land with a little bounce: the success tick, the headline finale. */
  bouncy: { type: "spring" as const, stiffness: 380, damping: 17, mass: 0.9 },
  /** A card growing into a page. */
  shared: { type: "spring" as const, stiffness: 260, damping: 30, mass: 1 },
};

/** A light buzz on phones that support it; silent everywhere else. */
export function haptic(pattern: number | number[] = 10) {
  try {
    navigator.vibrate?.(pattern);
  } catch {
    /* not allowed here */
  }
}
