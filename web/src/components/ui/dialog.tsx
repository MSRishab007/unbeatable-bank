import * as React from "react";
import * as D from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * One modal for the whole app. On a phone it is a sheet that springs up from the bottom;
 * from 640px it is a dialog in the middle of the screen.
 */
export function Modal({ open, onOpenChange, title, description, children, className, dismissible = true, bare = false }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: React.ReactNode;
  description?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  dismissible?: boolean;
  /** No padding or visible heading: the content draws its own (the scanner). */
  bare?: boolean;
}) {
  return (
    <D.Root open={open} onOpenChange={(o) => (dismissible || o ? onOpenChange(o) : undefined)}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-50 bg-ink/50 data-[state=open]:animate-fade-in" />
        <D.Content
          onEscapeKeyDown={(e) => !dismissible && e.preventDefault()}
          onPointerDownOutside={(e) => !dismissible && e.preventDefault()}
          className={cn(
            "fixed z-50 bg-surface shadow-float outline-none",
            "inset-x-0 bottom-0 max-h-[94dvh] overflow-y-auto rounded-t-panel data-[state=open]:animate-sheet-up",
            "sm:inset-auto sm:top-1/2 sm:left-1/2 sm:w-[min(92vw,460px)] sm:-translate-x-1/2 sm:-translate-y-1/2 sm:rounded-panel sm:data-[state=open]:animate-pop-in",
            !bare && "px-5 pt-3 pb-[max(1.5rem,env(safe-area-inset-bottom))] sm:p-7",
            className,
          )}
        >
          {!bare && <div className="mx-auto mb-4 h-1.5 w-10 rounded-full bg-line sm:hidden" aria-hidden />}
          <div className={cn(bare ? "sr-only" : "mb-5 pr-9")}>
            <D.Title className="font-display text-xl font-semibold tracking-tight text-ink">{title}</D.Title>
            {description ? <D.Description className="mt-1.5 text-[15px] leading-relaxed text-muted">{description}</D.Description>
              : <D.Description className="sr-only">{typeof title === "string" ? title : "Dialog"}</D.Description>}
          </div>
          {children}
          {dismissible && (
            <D.Close className={cn("press absolute top-4 right-4 rounded-full p-2 sm:top-6 sm:right-6",
              bare ? "on-dark bg-night/70 text-on-night hover:bg-night" : "text-muted hover:bg-sunken hover:text-ink")} aria-label="Close">
              <X className="size-5" />
            </D.Close>
          )}
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
