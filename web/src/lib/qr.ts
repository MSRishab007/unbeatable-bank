// Reading QR codes. The browser's BarcodeDetector does it natively where it exists (Chrome on
// Android and macOS, Samsung Internet); everywhere else the jsqr package is loaded on demand.
// Both run in this page from this origin, so the CSP (script-src 'self') is untouched.

type Detector = { detect(source: CanvasImageSource | ImageBitmap | ImageData): Promise<{ rawValue: string }[]> };
type DetectorClass = { new (init: { formats: string[] }): Detector; getSupportedFormats?(): Promise<string[]> };

let native: Promise<Detector | null> | null = null;
function nativeDetector(): Promise<Detector | null> {
  native ||= (async () => {
    const BD = (window as unknown as { BarcodeDetector?: DetectorClass }).BarcodeDetector;
    if (!BD) return null;
    try {
      const formats = (await BD.getSupportedFormats?.()) ?? ["qr_code"];
      return formats.includes("qr_code") ? new BD({ formats: ["qr_code"] }) : null;
    } catch {
      return null;
    }
  })();
  return native;
}

let scratch: HTMLCanvasElement | null = null;
async function withJsqr(source: CanvasImageSource, width: number, height: number): Promise<string | null> {
  const { default: jsQR } = await import("jsqr");
  // Big frames only slow jsQR down; a QR code is still readable well below 800px.
  const scale = Math.min(1, 800 / Math.max(width, height));
  scratch ||= document.createElement("canvas");
  scratch.width = Math.max(1, Math.round(width * scale));
  scratch.height = Math.max(1, Math.round(height * scale));
  const ctx = scratch.getContext("2d", { willReadFrequently: true })!;
  ctx.fillStyle = "white"; // a transparent PNG would otherwise decode as black on black
  ctx.fillRect(0, 0, scratch.width, scratch.height);
  ctx.drawImage(source, 0, 0, scratch.width, scratch.height);
  const img = ctx.getImageData(0, 0, scratch.width, scratch.height);
  return jsQR(img.data, img.width, img.height)?.data || null;
}

/** The text of the QR code in one camera frame, or null if there isn't one. */
export async function readFrame(video: HTMLVideoElement): Promise<string | null> {
  if (!video.videoWidth) return null;
  const d = await nativeDetector();
  if (d) {
    try {
      return (await d.detect(video))[0]?.rawValue || null;
    } catch {
      /* fall through to jsqr */
    }
  }
  return withJsqr(video, video.videoWidth, video.videoHeight);
}

/** The text of the QR code in a picture. Throws if the file isn't a picture the browser can open. */
export async function readPicture(file: Blob): Promise<string | null> {
  const bitmap = await createImageBitmap(file);
  try {
    const d = await nativeDetector();
    if (d) {
      try {
        const hit = (await d.detect(bitmap))[0]?.rawValue;
        if (hit) return hit;
      } catch {
        /* fall through to jsqr */
      }
    }
    return await withJsqr(bitmap, bitmap.width, bitmap.height);
  } finally {
    bitmap.close();
  }
}

export const cameraAvailable = () => !!navigator.mediaDevices?.getUserMedia && window.isSecureContext;
