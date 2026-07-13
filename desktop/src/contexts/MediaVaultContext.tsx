/**
 * MediaVaultContext — ONE useMediaVault() instance for the whole app.
 *
 * The vault owns lock state, decrypted blob URLs and the item list. A second
 * instance would hold a stale "unlocked" flag and its own decrypted URLs, so
 * locking in one place would leave plaintext bytes on screen in another. Same
 * doctrine as MediaLibraryContext: one store, one truth.
 */

import { createContext, useContext } from "react";
import { useMediaVault } from "@/hooks/use-media-vault";
import type {
  MediaVaultState,
  MediaVaultActions,
} from "@/hooks/use-media-vault";

const Ctx = createContext<[MediaVaultState, MediaVaultActions] | null>(null);

export function MediaVaultProvider({ children }: { children: React.ReactNode }) {
  const value = useMediaVault();
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/** The shared media-vault store. Throws outside MediaVaultProvider. */
export function useMediaVaultApp(): [MediaVaultState, MediaVaultActions] {
  const ctx = useContext(Ctx);
  if (!ctx) {
    throw new Error("useMediaVaultApp must be used within MediaVaultProvider");
  }
  return ctx;
}
