import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap font-semibold transition-[background,color,box-shadow,transform] duration-150 active:scale-[0.98] disabled:opacity-50 disabled:active:scale-100 [&_svg]:size-[1.15em] [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary: "bg-cobalt text-white hover:bg-cobalt-deep shadow-[0_1px_0_rgba(255,255,255,.25)_inset,0_6px_16px_-6px_rgba(45,42,230,.55)]",
        go: "bg-go text-white hover:bg-go-deep shadow-[0_6px_16px_-6px_rgba(18,166,99,.6)]",
        secondary: "bg-surface text-ink border border-line hover:border-ink-2/30 hover:bg-mist",
        ghost: "text-ink-2 hover:bg-ink/5",
        danger: "bg-danger text-white hover:brightness-95",
        "danger-ghost": "text-danger hover:bg-bad-bg",
        link: "text-cobalt underline-offset-4 hover:underline px-0 h-auto",
        volt: "bg-volt text-ink hover:brightness-95",
        light: "bg-white text-cobalt-ink hover:bg-white/90",
      },
      size: {
        sm: "h-9 rounded-full px-3.5 text-sm",
        md: "h-11 rounded-full px-5 text-[15px]",
        lg: "h-14 rounded-full px-7 text-base",
        icon: "size-10 rounded-full",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, type, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        ref={ref}
        type={asChild ? undefined : (type ?? "button")}
        className={cn(buttonVariants({ variant, size }), className)}
        {...props}
      />
    );
  },
);
Button.displayName = "Button";
export { buttonVariants };
