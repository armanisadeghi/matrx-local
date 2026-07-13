/**
 * MediaLibraryContext — ONE useMediaLibrary() instance for the whole app.
 *
 * Before this, the Library section, the Gallery variant and the Studio variant
 * each called useMediaLibrary() directly, giving three independent stores of
 * the same engine items. Deleting an image in one left it on screen in the
 * other two, and moving one to the vault refreshed only the store that
 * happened to trigger it — the exact partial-state class of bug this context
 * exists to make impossible.
 *
 * Pages/sections call useMediaLibraryApp(); nothing calls useMediaLibrary().
 */

import { createContext, useContext } from "react";
import { useMediaLibrary } from "@/hooks/use-media-library";
import type {
  MediaLibraryState,
  MediaLibraryActions,
} from "@/hooks/use-media-library";

const Ctx = createContext<[MediaLibraryState, MediaLibraryActions] | null>(null);

export function MediaLibraryProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const value = useMediaLibrary();
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/** The shared media-library store. Throws outside MediaLibraryProvider. */
export function useMediaLibraryApp(): [MediaLibraryState, MediaLibraryActions] {
  const ctx = useContext(Ctx);
  if (!ctx) {
    throw new Error(
      "useMediaLibraryApp must be used within MediaLibraryProvider",
    );
  }
  return ctx;
}
