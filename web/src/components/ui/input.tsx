import * as React from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

const FIELD = "h-12 w-full rounded-field border border-line bg-surface px-4 text-[15px] text-ink outline-none transition-[border-color,box-shadow] duration-fast placeholder:text-muted focus-visible:outline-none focus:border-ink focus:shadow-[0_0_0_4px_color-mix(in_srgb,var(--color-ink)_10%,transparent)] disabled:opacity-60";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input ref={ref} className={cn(FIELD, "aria-[invalid=true]:border-danger", className)} {...props} />
  ),
);
Input.displayName = "Input";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => <textarea ref={ref} className={cn(FIELD, "h-auto py-3", className)} {...props} />,
);
Textarea.displayName = "Textarea";

export const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, ...props }, ref) => (
    <div className="relative">
      <select ref={ref} className={cn(FIELD, "appearance-none pr-11", className)} {...props} />
      <ChevronDown aria-hidden className="pointer-events-none absolute top-1/2 right-4 size-4 -translate-y-1/2 text-muted" />
    </div>
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
      {error ? <p role="alert" className="text-[13px] font-medium text-danger">{error}</p>
        : hint ? <div className="text-[13px] text-muted">{hint}</div> : null}
    </div>
  );
}
