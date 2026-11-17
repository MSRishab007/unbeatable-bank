import * as React from "react";
import { OTPInput, OTPInputContext } from "input-otp";
import { motion } from "motion/react";
import { spring } from "@/lib/motion";
import { cn } from "@/lib/utils";

/** shadcn/ui's InputOTP: one real input drawn as separate boxes, so paste and auto-advance just work. */
export const InputOTP = React.forwardRef<
  React.ElementRef<typeof OTPInput>,
  React.ComponentPropsWithoutRef<typeof OTPInput>
>(({ className, containerClassName, ...props }, ref) => (
  <OTPInput
    ref={ref}
    containerClassName={cn("flex items-center gap-2 has-[:disabled]:opacity-50", containerClassName)}
    className={cn("disabled:cursor-not-allowed", className)}
    {...props}
  />
));
InputOTP.displayName = "InputOTP";

export function InputOTPGroup({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex items-center gap-1.5 sm:gap-2", className)} {...props} />;
}

/** One box. `mask` shows a dot instead of the digit (PINs). Each digit pops in as it is typed. */
export function InputOTPSlot({ index, mask, className, invalid }: {
  index: number; mask?: boolean; className?: string; invalid?: boolean;
}) {
  const ctx = React.useContext(OTPInputContext);
  const slot = ctx.slots[index]!;
  return (
    <div
      className={cn(
        "relative flex h-14 w-11 items-center justify-center rounded-field border-2 bg-surface font-display text-xl font-semibold text-ink transition-[border-color,box-shadow,background-color] duration-fast num sm:w-12",
        slot.isActive ? "border-ink shadow-[0_0_0_4px_color-mix(in_srgb,var(--color-ink)_10%,transparent)]" : slot.char ? "border-ink/30" : "border-line bg-bg",
        invalid && "border-danger bg-danger-soft",
        className,
      )}
    >
      {slot.char && (
        <motion.span key={slot.char + index} initial={{ scale: 0.4, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={spring.bouncy}>
          {mask ? <span className="block size-3 rounded-full bg-ink" /> : slot.char}
        </motion.span>
      )}
      {slot.hasFakeCaret && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <div className="h-6 w-0.5 animate-caret bg-ink" />
        </div>
      )}
    </div>
  );
}
