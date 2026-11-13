import * as React from "react";
import * as D from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * One modal for the whole app: a bottom sheet on phones, a centred card on wider screens.
 */
export function Modal({ open, onOpenChange, title, description, children, className, dismissible = true }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: React.ReactNode;
  description?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  dismissible?: boolean;
}) {
  return (
    <D.Root open={open} onOpenChange={(o) => (dismissible || o ? onOpenChange(o) : undefined)}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-50 bg-ink/45 backdrop-blur-[2px] data-[state=open]:animate-[fade-in_.18s_ease-out]" />
        <D.Content
          onEscapeKeyDown={(e) => !dismissible && e.preventDefault()}
          onPointerDownOutside={(e) => !dismissible && e.preventDefault()}
          className={cn(
            "fixed z-50 bg-surface shadow-2xl outline-none",
            "inset-x-0 bottom-0 max-h-[92dvh] overflow-y-auto rounded-t-[28px] px-5 pt-3 pb-[max(1.5rem,env(safe-area-inset-bottom))]",
            "data-[state=open]:animate-[sheet-up_.28s_cubic-bezier(.2,.9,.25,1)]",
            "sm:inset-auto sm:top-1/2 sm:left-1/2 sm:w-[min(92vw,460px)] sm:-translate-x-1/2 sm:-translate-y-1/2 sm:rounded-[28px] sm:p-7",
            "sm:data-[state=open]:animate-[pop-in_.2s_cubic-bezier(.2,.9,.25,1)]",
            className,
          )}
        >
          <div className="mx-auto mb-4 h-1.5 w-10 rounded-full bg-line sm:hidden" aria-hidden />
          <div className="mb-5 pr-8">
            <D.Title className="font-display text-xl font-semibold tracking-tight text-ink">{title}</D.Title>
            {description ? <D.Description className="mt-1.5 text-[15px] leading-relaxed text-muted">{description}</D.Description>
              : <D.Description className="sr-only">{typeof title === "string" ? title : "Dialog"}</D.Description>}
          </div>
          {children}
          {dismissible && (
            <D.Close className="absolute top-5 right-5 rounded-full p-2 text-muted hover:bg-mist hover:text-ink sm:top-6" aria-label="Close">
              <X className="size-5" />
            </D.Close>
          )}
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
