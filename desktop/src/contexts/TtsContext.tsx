/**
 * TtsContext
 *
 * Provides a single shared useTts() instance to the entire app.
 * State is initialized once when the provider mounts and is never reset
 * by navigation, tab switches, focus/blur, or window visibility changes.
 * The TextToSpeech page and any other consumer read from this shared state.
 */

import { createContext, useContext } from "react";
import { useTts } from "@/hooks/use-tts";
import type { UseTtsState, UseTtsActions } from "@/hooks/use-tts";

const TtsAppContext = createContext<[UseTtsState, UseTtsActions] | null>(null);

export function TtsProvider({ children }: { children: React.ReactNode }) {
  const tts = useTts();
  return <TtsAppContext.Provider value={tts}>{children}</TtsAppContext.Provider>;
}

/** Use the shared app-level TTS state. Throws if used outside TtsProvider. */
export function useTtsApp(): [UseTtsState, UseTtsActions] {
  const ctx = useContext(TtsAppContext);
  if (!ctx) throw new Error("useTtsApp must be used within TtsProvider");
  return ctx;
}

/**
 * Non-throwing variant: returns the shared TTS state, or null when there is no
 * TtsProvider in the tree. Use this in surfaces where TTS is optional so the
 * hook is still called unconditionally (rules-of-hooks safe) instead of being
 * wrapped in a try/catch, which silently hides the missing provider.
 */
export function useTtsAppOptional(): [UseTtsState, UseTtsActions] | null {
  return useContext(TtsAppContext);
}
