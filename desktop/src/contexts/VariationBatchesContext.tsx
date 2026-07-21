import { createContext, useContext } from "react";
import { useVariationBatches } from "@/hooks/use-variation-batches";
import type {
  VariationBatchesActions,
  VariationBatchesState,
} from "@/hooks/use-variation-batches";

const Ctx = createContext<
  [VariationBatchesState, VariationBatchesActions] | null
>(null);

export function VariationBatchesProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const value = useVariationBatches();
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useVariationBatchesApp(): [
  VariationBatchesState,
  VariationBatchesActions,
] {
  const ctx = useContext(Ctx);
  if (!ctx) {
    throw new Error(
      "useVariationBatchesApp must be used within VariationBatchesProvider",
    );
  }
  return ctx;
}
