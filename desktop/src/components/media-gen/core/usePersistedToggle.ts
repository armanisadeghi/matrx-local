import { useCallback, useEffect, useState } from "react";

export const IMAGE_GEN_PANEL_KEYS = {
  negative: "matrx-image-gen-show-negative",
  inputImage: "matrx-image-gen-show-input-image",
  loras: "matrx-image-gen-show-loras",
  advanced: "matrx-image-gen-show-advanced",
} as const;

function readPersistedToggle(key: string, defaultOpen: boolean): boolean {
  try {
    const stored = localStorage.getItem(key);
    if (stored === "1") return true;
    if (stored === "0") return false;
  } catch {
    // ignore
  }
  return defaultOpen;
}

/** Panel open/closed preference persisted in localStorage. */
export function usePersistedToggle(storageKey: string, defaultOpen = false) {
  const [open, setOpen] = useState(() =>
    readPersistedToggle(storageKey, defaultOpen),
  );

  useEffect(() => {
    try {
      localStorage.setItem(storageKey, open ? "1" : "0");
    } catch {
      // ignore
    }
  }, [open, storageKey]);

  const toggle = useCallback(() => setOpen((value) => !value), []);
  const reveal = useCallback(() => setOpen(true), []);

  return { open, setOpen, toggle, reveal } as const;
}
