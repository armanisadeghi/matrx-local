import { createContext, useContext } from "react";
import { useSavedPrompts } from "@/hooks/use-saved-prompts";
import type {
  SavedPromptsActions,
  SavedPromptsState,
} from "@/hooks/use-saved-prompts";

const Ctx = createContext<[SavedPromptsState, SavedPromptsActions] | null>(
  null,
);

export function SavedPromptsProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const value = useSavedPrompts();
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSavedPromptsApp(): [SavedPromptsState, SavedPromptsActions] {
  const ctx = useContext(Ctx);
  if (!ctx) {
    throw new Error(
      "useSavedPromptsApp must be used within SavedPromptsProvider",
    );
  }
  return ctx;
}
