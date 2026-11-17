import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Bolt, Mark } from "./Logo";
import { spring } from "@/lib/motion";
import { cn, reducedMotion } from "@/lib/utils";

/**
 * The landing headline. "We are" and the "un" mark stay put; the rest of each promise rolls
 * through, and each final word acts out what it says. It ends on "Unbeatable Bank" and stays.
 *
 *   click or any key   skips to the last line
 *   reduced motion     shows only the last line
 *   screen readers     get one sentence, not ten changing ones
 */
const LINES: [rest: string, word: string][] = [
  ["believably", "Fast"],
  ["thinkably", "Secure"],
  ["defeatably", "Ahead"],
  ["mistakably", "Simple"],
  ["shakeably", "Reliable"],
  ["failingly", "Available"],
  ["blinkingly", "Watchful"],
  ["stoppably", "Scalable"],
  ["questionably", "Transparent"],
  ["beatable", "Bank"],
];
const LAST = LINES.length - 1;
const HOLD = 1450;

const WORD = "inline-block whitespace-nowrap font-extrabold tracking-[-0.045em]";
const Letters = ({ text, children }: { text: string; children: (ch: string, i: number, n: number) => React.ReactNode }) =>
  <>{[...text].map((ch, i) => children(ch, i, text.length))}</>;

// ------------------------------------------------------------------ the ten effects

/** 1. Races in from the right, leaning and blurred, trailing speed lines; then stops dead. */
function Fast({ word }: { word: string }) {
  return (
    <span className="relative inline-block">
      <motion.span
        className={cn(WORD, "italic")}
        initial={{ x: "70vw", skewX: -22, filter: "blur(10px)" }}
        animate={{ x: 0, skewX: [-22, -22, 7, 0], filter: "blur(0px)" }}
        transition={{ duration: 0.46, ease: [0.3, 0, 0.1, 1], skewX: { duration: 0.6, times: [0, 0.72, 0.84, 1] } }}
      >
        {word}
      </motion.span>
      {[["18%", "1.1em", 0], ["48%", "0.7em", 0.05], ["76%", "1.5em", 0.02]].map(([top, width, delay]) => (
        <motion.span
          key={top}
          className="absolute left-[calc(100%+0.18em)] h-[0.07em] origin-left rounded-full bg-current"
          style={{ top, width }}
          initial={{ scaleX: 1, opacity: 0 }}
          animate={{ scaleX: [1, 1, 0], opacity: [0, 1, 1] }}
          transition={{ duration: 0.42, times: [0, 0.15, 1], delay: 0.36 + (delay as number), ease: "easeOut" }}
        />
      ))}
    </span>
  );
}

/** 2. Letters start apart, slide shut, a padlock closes, and the word gives one tight shake. */
function Secure({ word }: { word: string }) {
  const mid = (word.length - 1) / 2;
  return (
    <motion.span className="inline-flex items-baseline" animate={{ x: [0, -5, 5, -3, 3, 0] }} transition={{ delay: 0.58, duration: 0.28 }}>
      <span className={WORD}>
        <Letters text={word}>
          {(ch, i) => (
            <motion.span key={i} className="inline-block" initial={{ x: `${(i - mid) * 0.42}em`, opacity: 0.35 }} animate={{ x: 0, opacity: 1 }}
              transition={{ delay: 0.16, duration: 0.4, ease: [0.7, 0, 0.84, 0] }}>
              {ch}
            </motion.span>
          )}
        </Letters>
      </span>
      <svg viewBox="0 0 24 24" aria-hidden className="ml-[0.16em] h-[0.62em] w-[0.62em] self-center overflow-visible">
        <motion.path d="M7.5 11V7.5a4.5 4.5 0 0 1 9 0V11" fill="none" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round"
          initial={{ y: -4.5 }} animate={{ y: 0 }} transition={{ delay: 0.54, duration: 0.09, ease: "easeIn" }} />
        <rect x="4" y="10.5" width="16" height="11.5" rx="3.2" fill="currentColor" />
      </svg>
    </motion.span>
  );
}

/** 3. Comes up from behind the previous word and passes it; that one slips back and fades. */
function Ahead({ word, prev }: { word: string; prev: string }) {
  return (
    <span className="relative inline-block">
      <motion.span aria-hidden className={cn(WORD, "absolute top-0 left-0")} initial={{ x: 0, opacity: 1, scale: 1 }}
        animate={{ x: "-38%", opacity: 0, scale: 0.9 }} transition={{ duration: 0.75, ease: [0.4, 0, 1, 1] }}>
        {prev}
      </motion.span>
      <motion.span className={WORD} initial={{ x: "-115%", opacity: 0 }} animate={{ x: ["-115%", "7%", "0%"], opacity: [0, 1, 1] }}
        transition={{ duration: 0.8, times: [0, 0.72, 1], ease: [0.2, 0.7, 0.3, 1] }}>
        {word}
      </motion.span>
    </span>
  );
}

/** 4. Nothing. Light type, there at once. The contrast with the other nine is the point. */
function Simple({ word }: { word: string }) {
  return <span className="inline-block whitespace-nowrap font-sans font-light tracking-[-0.03em]">{word}</span>;
}

/** 5. The line above wobbles (see `wobble` below). This word does not move at all. */
function Reliable({ word }: { word: string }) {
  return <span className={WORD}>{word}</span>;
}

/** 6. Letters come on one at a time, each under its own status light, and stay lit. */
function Available({ word }: { word: string }) {
  return (
    <span className={WORD}>
      <Letters text={word}>
        {(ch, i) => (
          <span key={i} className="relative inline-block">
            <span className="absolute -bottom-[0.13em] left-1/2 size-[0.1em] -translate-x-1/2 rounded-full bg-current opacity-20" />
            <motion.span className="absolute -bottom-[0.13em] left-1/2 size-[0.1em] -translate-x-1/2 rounded-full bg-surface"
              initial={{ opacity: 0, scale: 0.2 }} animate={{ opacity: 1, scale: [0.2, 1.7, 1] }} transition={{ delay: 0.15 + i * 0.085, duration: 0.3 }} />
            <motion.span className="inline-block" initial={{ opacity: 0.2 }} animate={{ opacity: 1 }} transition={{ delay: 0.15 + i * 0.085, duration: 0.12 }}>
              {ch}
            </motion.span>
          </span>
        )}
      </Letters>
    </span>
  );
}

/** 7. A scan band sweeps across the word and back, like a camera checking it. */
function Watchful({ word }: { word: string }) {
  return (
    <span className="relative inline-block">
      <motion.span aria-hidden className="absolute -inset-y-[0.04em] w-[0.5em] border-r-[0.05em] border-current bg-surface"
        initial={{ left: "-0.5em", opacity: 0 }} animate={{ left: ["-0.5em", "100%", "-0.5em"], opacity: [1, 1, 0] }}
        transition={{ delay: 0.12, duration: 1.15, times: [0, 0.5, 1], ease: "easeInOut" }} />
      <span className={cn(WORD, "relative")}>{word}</span>
    </span>
  );
}

/** 8. Grows in four hard steps, like replicas being added. */
function Scalable({ word }: { word: string }) {
  return (
    <motion.span className={cn(WORD, "origin-bottom-left")} initial={{ scale: 0.28 }}
      animate={{ scale: [0.28, 0.28, 0.52, 0.52, 0.76, 0.76, 1] }}
      transition={{ duration: 0.95, times: [0, 0.2, 0.24, 0.46, 0.5, 0.72, 0.76], ease: "linear" }}>
      {word}
    </motion.span>
  );
}

/** 9. Frosted and blurred, then perfectly clear. */
function Transparent({ word }: { word: string }) {
  return (
    <span className="relative inline-block">
      <motion.span className={WORD} initial={{ filter: "blur(0.22em)", opacity: 0.4 }} animate={{ filter: "blur(0em)", opacity: 1 }}
        transition={{ delay: 0.1, duration: 1, ease: [0.4, 0, 0.2, 1] }}>
        {word}
      </motion.span>
      <motion.span aria-hidden className="absolute -inset-x-[0.12em] inset-y-[0.02em] rounded-[0.14em] bg-surface/45"
        initial={{ opacity: 1 }} animate={{ opacity: 0 }} transition={{ delay: 0.1, duration: 0.9 }} />
    </span>
  );
}

/** 10. The finale: drops in with weight, a ring goes out, the bolt strikes, and it stays. */
function Bank({ word, still }: { word: string; still: boolean }) {
  return (
    <span className="relative inline-flex items-baseline">
      {!still && (
        <motion.span aria-hidden className="absolute top-1/2 left-[40%] size-[1em] -translate-x-1/2 -translate-y-1/2 rounded-full border-[0.05em] border-current"
          initial={{ scale: 0.3, opacity: 0.7 }} animate={{ scale: 4.2, opacity: 0 }} transition={{ delay: 0.16, duration: 0.7, ease: "easeOut" }} />
      )}
      <motion.span className={WORD} initial={still ? false : { y: "-85%", scale: 1.45, opacity: 0 }} animate={{ y: 0, scale: 1, opacity: 1 }} transition={spring.bouncy}>
        {word}
      </motion.span>
      <motion.span className="absolute bottom-[0.02em] left-[calc(100%+0.02em)] block h-[0.92em] w-[0.66em] text-sun [&_path]:stroke-hero-ink [&_path]:[stroke-width:3.5] [&_path]:[stroke-linejoin:round] [&_svg]:overflow-visible"
        initial={still ? false : { scale: 0, rotate: -30, opacity: 0 }} animate={{ scale: still ? 1 : [0, 2, 1], rotate: still ? 0 : [-30, 10, 0], opacity: 1 }}
        transition={{ delay: 0.2, duration: 0.5, times: [0, 0.45, 1], ease: "easeOut" }}>
        <Bolt className="size-full" />
      </motion.span>
    </span>
  );
}

function Effect({ i, still }: { i: number; still: boolean }) {
  const word = LINES[i]![1];
  switch (i) {
    case 0: return <Fast word={word} />;
    case 1: return <Secure word={word} />;
    case 2: return <Ahead word={word} prev={LINES[1]![1]} />;
    case 3: return <Simple word={word} />;
    case 4: return <Reliable word={word} />;
    case 5: return <Available word={word} />;
    case 6: return <Watchful word={word} />;
    case 7: return <Scalable word={word} />;
    case 8: return <Transparent word={word} />;
    default: return <Bank word={word} still={still} />;
  }
}

// ------------------------------------------------------------------ the headline

export function Headline({ onSettled, className }: { onSettled?: () => void; className?: string }) {
  const [still] = useState(reducedMotion);
  const [i, setI] = useState(still ? LAST : 0);
  const last = i === LAST;
  const skip = useCallback(() => setI(LAST), []);

  useEffect(() => {
    if (last) {
      const t = setTimeout(() => onSettled?.(), 900);
      return () => clearTimeout(t);
    }
    const t = setTimeout(() => setI((n) => n + 1), i === 0 ? HOLD + 350 : HOLD);
    return () => clearTimeout(t);
  }, [i, last, onSettled]);

  useEffect(() => {
    if (last) return;
    const onKey = (e: KeyboardEvent) => {
      if (!["Shift", "Control", "Alt", "Meta", "Tab"].includes(e.key)) skip();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [last, skip]);

  const quiet = still;
  return (
    <div className={cn("@container relative", className)}>
      <h1 onClick={last ? undefined : skip} className={cn("font-display text-[11.2cqw] leading-[1.02] text-hero-ink", !last && "cursor-pointer")}>
        <span className="sr-only">
          We are Unbeatable Bank: unbelievably fast, unthinkably secure, undefeatably ahead, unmistakably simple, unshakeably reliable,
          unfailingly available, unblinkingly watchful, unstoppably scalable and unquestionably transparent.
        </span>
        <span aria-hidden className="block">
          {/* Line 5 wobbles everything except its own word. */}
          <motion.span key={i === 4 ? "wobble" : "steady"} className="block origin-bottom-left"
            animate={i === 4 ? { rotate: [0, -2.4, 1.7, -0.9, 0.4, 0], x: [0, -6, 5, -3, 1, 0] } : undefined}
            transition={{ delay: 0.5, duration: 0.7, ease: "easeInOut" }}>
            <span className="mb-[0.42em] block font-sans text-[0.36em] font-medium tracking-[-0.01em]">We are</span>
            <span className="flex items-baseline">
              <motion.span className="mr-[0.035em] inline-block h-[0.7em] w-[0.764em] shrink-0 translate-y-[0.1em]"
                animate={last && !quiet ? { scale: [1, 1.22, 1] } : { scale: 1 }} transition={{ delay: 0.12, duration: 0.45, ease: "easeOut" }}>
                <Mark variant="bare" crop title={null} draw={!still} className="size-full overflow-visible" />
              </motion.span>
              <span className="relative grid overflow-hidden py-[0.12em] -my-[0.12em] font-medium tracking-[-0.055em]">
                {/* Every line, invisible, in one grid cell: the box is always as wide as the longest. */}
                {LINES.map(([rest]) => <span key={rest} className="invisible col-start-1 row-start-1 whitespace-nowrap">{rest}</span>)}
                <AnimatePresence initial={false} mode="popLayout">
                  <motion.span key={i} className="col-start-1 row-start-1 whitespace-nowrap"
                    initial={quiet ? false : { y: "105%" }} animate={{ y: "0%" }} exit={quiet ? { opacity: 0 } : { y: "-105%" }}
                    transition={spring.page}>
                    {LINES[i]![0]}
                  </motion.span>
                </AnimatePresence>
              </span>
            </span>
          </motion.span>
          <span className="mt-[0.06em] grid text-[1.24em] leading-[1.06]">
            {LINES.map(([, word], n) => (
              <span key={word} className={cn("invisible col-start-1 row-start-1", n === 3 ? "font-sans font-light" : WORD)}>{word}</span>
            ))}
            <span key={i} className="col-start-1 row-start-1 justify-self-start">
              <Effect i={i} still={still} />
            </span>
          </span>
        </span>
      </h1>
      {!last && (
        <button type="button" onClick={skip} className="press absolute -bottom-9 left-0 rounded-full text-[13px] font-semibold text-hero-ink underline underline-offset-4">
          Skip the intro
        </button>
      )}
    </div>
  );
}
