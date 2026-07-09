/**
 * MediaGenContext
 *
 * Provides a single shared useMediaGen() instance to the entire app so that
 * media-generation state — most importantly a RUNNING VIDEO JOB and any
 * generated results — survives tab switches and page navigation.  A 10-minute
 * video job must never be orphaned because the user clicked another tab.
 *
 * Pattern mirrors TtsProvider / LlmProvider: pages call useMediaGenApp()
 * (context), never useMediaGen() directly (which would create a second,
 * throwaway store instance).
 */

import { createContext, useContext } from "react";
import { useMediaGen } from "@/hooks/use-media-gen";
import type { MediaGenState, MediaGenActions } from "@/hooks/use-media-gen";

const MediaGenAppContext = createContext<
  [MediaGenState, MediaGenActions] | null
>(null);

export function MediaGenProvider({ children }: { children: React.ReactNode }) {
  const mediaGen = useMediaGen();
  return (
    <MediaGenAppContext.Provider value={mediaGen}>
      {children}
    </MediaGenAppContext.Provider>
  );
}

/** Use the shared app-level media-gen state. Throws outside MediaGenProvider. */
export function useMediaGenApp(): [MediaGenState, MediaGenActions] {
  const ctx = useContext(MediaGenAppContext);
  if (!ctx) {
    throw new Error("useMediaGenApp must be used within MediaGenProvider");
  }
  return ctx;
}
