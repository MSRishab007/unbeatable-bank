import * as React from "react";
import { OTPInput, OTPInputContext } from "input-otp";
import { cn } from "@/lib/utils";

/** shadcn/ui's InputOTP: one real input, drawn as separate boxes. `mask` shows dots (PINs). */
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
  return <div className={cn("flex items-center gap-2", className)} {...props} />;
}

export function InputOTPSlot({ index, mask, className, invalid }: {
  index: number; mask?: boolean; className?: string; invalid?: boolean;
}) {
  const ctx = React.useContext(OTPInputContext);
  const slot = ctx.slots[index]!;
  return (
    <div
      className={cn(
        "relative flex size-12 sm:size-14 items-center justify-center rounded-2xl border-2 bg-surface text-xl font-semibold transition-all num",
        slot.isActive ? "border-cobalt ring-4 ring-cobalt/12" : "border-line",
        slot.char && !slot.isActive && "border-ink/25",
        invalid && "border-danger ring-danger/12",
        className,
      )}
    >
      {slot.char ? (mask ? <span className="size-3 rounded-full bg-ink" /> : slot.char) : null}
      {slot.hasFakeCaret && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <div className="h-6 w-0.5 animate-[caret_1s_steps(2)_infinite] bg-cobalt" />
        </div>
      )}
    </div>
  );
}
