import { useCallback, useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import { Camera, Check, ImageUp, ScanLine } from "lucide-react";
import { Modal } from "./ui/dialog";
import { Button } from "./ui/button";
import { haptic, spring } from "@/lib/motion";
import { parsePayCode, type PayCode } from "@/lib/paycode";
import { cameraAvailable, readFrame, readPicture } from "@/lib/qr";
import { cn } from "@/lib/utils";

type State =
  | { kind: "starting" | "scanning" | "found" }
  | { kind: "problem"; text: string; camera: boolean };

const NOT_OURS = "That's a QR code, but not an Unbeatable Bank one. Ask the person you're paying to open Receive in their app and show you that code.";

/**
 * Scan to pay. Opens the back camera in a viewfinder and reads frames until it sees an
 * Unbeatable Bank code; a picture of a code can be uploaded instead (the camera needs
 * HTTPS or localhost, and permission). `onCode` gets the payee, amount and note.
 */
export function Scanner({ open, onOpenChange, onCode }: { open: boolean; onOpenChange: (open: boolean) => void; onCode: (code: PayCode) => void }) {
  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Scan a QR code" description="Point the camera at an Unbeatable Bank QR code, or upload a picture of one."
      bare className="on-dark flex flex-col overflow-hidden bg-night text-on-night max-sm:h-[92dvh] sm:w-[min(92vw,430px)]">
      {open && <View onCode={(c) => { onOpenChange(false); onCode(c); }} />}
    </Modal>
  );
}

function View({ onCode }: { onCode: (code: PayCode) => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const done = useRef(false);
  const [state, setState] = useState<State>({ kind: "starting" });
  const [note, setNote] = useState("");

  const stop = useCallback(() => {
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
  }, []);

  /** A decoded QR code: ours finishes the scan, anything else gets a clear message. */
  const accept = useCallback((text: string) => {
    const code = parsePayCode(text);
    if (!code) {
      setNote(NOT_OURS);
      return false;
    }
    done.current = true;
    haptic([10, 40, 20]);
    setNote("");
    setState({ kind: "found" });
    stop();
    setTimeout(() => onCode(code), 520);
    return true;
  }, [onCode, stop]);

  const start = useCallback(async () => {
    setNote("");
    if (!cameraAvailable()) {
      setState({ kind: "problem", camera: false, text: "The camera only works over a secure (https) connection. Upload a picture of the code instead." });
      return;
    }
    setState({ kind: "starting" });
    try {
      const s = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
      if (done.current) return s.getTracks().forEach((t) => t.stop());
      stream.current = s;
      const el = video.current!;
      el.srcObject = s;
      await el.play();
      setState({ kind: "scanning" });
    } catch (e) {
      const denied = (e as DOMException)?.name === "NotAllowedError";
      setState({ kind: "problem", camera: true, text: denied
        ? "The camera is blocked for this site. Allow it in your browser's site settings, or upload a picture of the code."
        : "We couldn't open a camera on this device. Upload a picture of the code instead." });
    }
  }, []);

  useEffect(() => {
    done.current = false;
    start();
    return () => {
      done.current = true;
      stop();
    };
  }, [start, stop]);

  // Read a frame, wait a beat, read the next: steady work without pinning the processor.
  useEffect(() => {
    if (state.kind !== "scanning") return;
    let live = true;
    let lastWrong = "";
    (async () => {
      while (live && !done.current) {
        const text = video.current ? await readFrame(video.current).catch(() => null) : null;
        if (!live || done.current) break;
        if (text && text !== lastWrong && !accept(text)) lastWrong = text;
        await new Promise((r) => setTimeout(r, 140));
      }
    })();
    return () => {
      live = false;
    };
  }, [state.kind, accept]);

  async function upload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    try {
      const text = await readPicture(file);
      if (!text) setNote("We couldn't find a QR code in that picture. Try a sharper one, with the whole code in view.");
      else accept(text);
    } catch {
      setNote("That file isn't a picture we can read. Choose a JPEG or PNG.");
    }
  }

  const found = state.kind === "found";
  const scanning = state.kind === "scanning";
  const message = note || (state.kind === "problem" ? state.text : found ? "Got it." : scanning ? "Hold the code inside the frame." : "Opening the camera…");
  return (
    <>
      <div className="relative min-h-[300px] flex-1 overflow-hidden bg-night-2 sm:aspect-square sm:flex-none">
        <video ref={video} playsInline muted aria-hidden className={cn("absolute inset-0 size-full object-cover transition-opacity duration-base", scanning || found ? "opacity-100" : "opacity-0")} />
        {/* The viewfinder: everything outside the frame is dimmed by the frame's own shadow. */}
        <div className="absolute inset-0 flex items-center justify-center">
          <motion.div initial={{ scale: 1.25, opacity: 0 }} animate={{ scale: found ? 0.94 : 1, opacity: 1 }} transition={spring.bouncy}
            className={cn("relative aspect-square w-[68%] max-w-[300px] rounded-card shadow-[0_0_0_100vmax_color-mix(in_srgb,var(--color-night)_62%,transparent)]", found ? "text-success" : note ? "text-accent" : "text-sun")}>
            {["top-0 left-0 border-t-4 border-l-4 rounded-tl-card", "top-0 right-0 border-t-4 border-r-4 rounded-tr-card",
              "bottom-0 left-0 border-b-4 border-l-4 rounded-bl-card", "bottom-0 right-0 border-b-4 border-r-4 rounded-br-card"].map((c) => (
              <span key={c} aria-hidden className={cn("absolute size-10 border-current transition-colors duration-base", c)} />
            ))}
            {scanning && <span aria-hidden className="absolute inset-x-3 h-[3px] -translate-y-1/2 animate-scan rounded-full bg-current" />}
            {found && (
              <motion.span initial={{ scale: 0 }} animate={{ scale: 1 }} transition={spring.bouncy} className="absolute inset-0 m-auto flex size-16 items-center justify-center rounded-full bg-success text-success-ink">
                <Check className="size-8" strokeWidth={3} />
              </motion.span>
            )}
            {state.kind === "problem" && <ScanLine aria-hidden className="absolute inset-0 m-auto size-14 text-on-night-muted" strokeWidth={1.5} />}
          </motion.div>
        </div>
      </div>
      <div className="space-y-4 px-5 pt-5 pb-[max(1.25rem,env(safe-area-inset-bottom))]">
        <div>
          <p className="font-display text-lg font-semibold tracking-tight">Scan a QR code</p>
          <p role="status" aria-live="polite" id="scan-status" className={cn("mt-1 min-h-10 text-sm leading-snug", note || state.kind === "problem" ? "text-sun" : "text-on-night-muted")}>{message}</p>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          <Button asChild variant="surface" className={cn(!(state.kind === "problem" && state.camera) && "sm:col-span-2")}>
            <label className="cursor-pointer has-[:focus-visible]:outline-[2.5px] has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-on-night has-[:focus-visible]:outline-solid">
              <ImageUp /> Upload a picture of a QR code
              <input id="qr-file" type="file" accept="image/*" className="sr-only" onChange={upload} />
            </label>
          </Button>
          {state.kind === "problem" && state.camera && <Button variant="sun" onClick={start}><Camera /> Try the camera again</Button>}
        </div>
      </div>
    </>
  );
}
