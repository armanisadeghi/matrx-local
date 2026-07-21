/**
 * ListLibraryContext — ONE useListLibrary() instance for the whole app.
 *
 * Named option lists are global creative assets (like the media library grid),
 * not batch-form state. They must be editable without an active image model.
 */

import { createContext, useContext } from "react";
import { useListLibrary } from "@/hooks/use-list-library";
import type {
  ListLibraryActions,
  ListLibraryState,
} from "@/hooks/use-list-library";

const Ctx = createContext<[ListLibraryState, ListLibraryActions] | null>(null);

export function ListLibraryProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const value = useListLibrary();
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useListLibraryApp(): [ListLibraryState, ListLibraryActions] {
  const ctx = useContext(Ctx);
  if (!ctx) {
    throw new Error(
      "useListLibraryApp must be used within ListLibraryProvider",
    );
  }
  return ctx;
}
