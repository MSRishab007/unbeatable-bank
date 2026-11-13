import * as React from "react";
import { cn } from "@/lib/utils";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        "h-12 w-full rounded-2xl border border-line bg-surface px-4 text-[15px] text-ink placeholder:text-muted/70",
        "transition-[border,box-shadow] outline-none focus-visible:outline-none focus:border-cobalt focus:ring-4 focus:ring-cobalt/12",
        "aria-[invalid=true]:border-danger disabled:opacity-60",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";

export const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, ...props }, ref) => (
    <select
      ref={ref}
      className={cn(
        "h-12 w-full appearance-none rounded-2xl border border-line bg-surface bg-no-repeat px-4 pr-10 text-[15px] text-ink outline-none",
        "focus-visible:outline-none focus:border-cobalt focus:ring-4 focus:ring-cobalt/12",
        "bg-[url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' fill='none'%3E%3Cpath d='M1 1.5 6 6.5l5-5' stroke='%236a6d88' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E\")] bg-[position:right_16px_center]",
        className,
      )}
      {...props}
    />
  ),
);
Select.displayName = "Select";

export function Field({ label, hint, error, children, htmlFor }: {
  label: React.ReactNode; hint?: React.ReactNode; error?: React.ReactNode; children: React.ReactNode; htmlFor?: string;
}) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="block text-[13px] font-semibold text-ink-2">{label}</label>
      {children}
      {error ? <p role="alert" className="text-[13px] text-danger">{error}</p>
        : hint ? <div className="text-[13px] text-muted">{hint}</div> : null}
    </div>
  );
}
