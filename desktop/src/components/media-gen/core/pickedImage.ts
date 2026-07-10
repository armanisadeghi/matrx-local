/**
 * PickedImage helpers — leaf module (no imports from shared.tsx or the
 * controllers) so both shared.tsx and the controllers can use it without an
 * import cycle. The ONE implementation of the 20 MB image-picking guard.
 */

import type { PickedImage } from "@/hooks/use-media-gen";

/** 20 MB guard shared by every image-picking path (base64 is ~1.33x). */
export const MAX_INPUT_IMAGE_BYTES = 20 * 1024 * 1024;

/**
 * Read a File into a PickedImage (base64 + preview data URL) with the 20 MB
 * guard. Errors are reported through `onError` — never silently dropped.
 */
export function readPickedImage(
  file: File,
  onOk: (image: PickedImage) => void,
  onError: (message: string) => void,
): void {
  if (file.size > MAX_INPUT_IMAGE_BYTES) {
    onError(
      `That image is ${(file.size / (1024 * 1024)).toFixed(1)} MB — please choose one under 20 MB.`,
    );
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    const dataUrl = typeof reader.result === "string" ? reader.result : "";
    const comma = dataUrl.indexOf(",");
    if (comma < 0) {
      onError("Could not read the selected image.");
      return;
    }
    onOk({
      name: file.name,
      base64: dataUrl.slice(comma + 1),
      previewUrl: dataUrl,
    });
  };
  reader.onerror = () => onError("Could not read the selected image.");
  reader.readAsDataURL(file);
}

/**
 * Convert an already-displayed image URL (blob:/data:) into a PickedImage —
 * the "Use as input" path from lightbox items, library cards and thumbnails.
 * Resolves null on failure (the failure is reported via onError).
 */
export async function pickedImageFromUrl(
  url: string,
  name: string,
  onError: (message: string) => void,
): Promise<PickedImage | null> {
  try {
    const resp = await fetch(url);
    const blob = await resp.blob();
    if (blob.size > MAX_INPUT_IMAGE_BYTES) {
      onError(
        `That image is ${(blob.size / (1024 * 1024)).toFixed(1)} MB — inputs must be under 20 MB.`,
      );
      return null;
    }
    const file = new File([blob], name, { type: blob.type || "image/png" });
    return await new Promise<PickedImage | null>((resolve) => {
      readPickedImage(
        file,
        (img) => resolve(img),
        (msg) => {
          onError(msg);
          resolve(null);
        },
      );
    });
  } catch (e) {
    onError(e instanceof Error ? e.message : "Could not read the image bytes.");
    return null;
  }
}
