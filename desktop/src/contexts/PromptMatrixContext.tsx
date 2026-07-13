/**
 * PromptMatrixProvider — ONE prompt matrix, shared by everything that renders
 * part of it.
 *
 * Two reasons this is a Context and not a hook call per component (repo
 * CLAUDE.md → "Persistent state belongs in Context, not page-level hooks"):
 *
 *  1. A half-built matrix — ten hand-typed options, a tuned strategy — must
 *     survive a tab switch. A per-component hook would throw it away on unmount.
 *  2. The matrix UI is deliberately SPLIT: the template/variables/strategy sit
 *     above the model's base settings, and the run-count + Queue button sit
 *     below them (you should see what you're queueing after you've set it up).
 *     Both halves need the same state.
 *
 * The target is rebuilt only when the model/LoRA catalog changes, so option
 * validation always reflects what is actually installed.
 */

import { createContext, useContext, useMemo, type ReactNode } from "react";

import { useMediaGenApp } from "@/contexts/MediaGenContext";
import {
  usePromptMatrix,
  type PromptMatrixActions,
  type PromptMatrixState,
} from "@/hooks/use-prompt-matrix";
import { createImageTarget, type MatrixTarget } from "@/lib/prompt-matrix";
import type { ImageGenerateInput } from "@/hooks/use-media-gen";

interface PromptMatrixContextValue {
  state: PromptMatrixState;
  actions: PromptMatrixActions;
  target: MatrixTarget<ImageGenerateInput>;
}

const PromptMatrixContext = createContext<PromptMatrixContextValue | null>(null);

export function PromptMatrixProvider({ children }: { children: ReactNode }) {
  const [mediaState] = useMediaGenApp();
  const { imageModels, loraList } = mediaState;

  const target = useMemo(
    () =>
      createImageTarget({
        models: imageModels,
        loras: loraList?.installed ?? [],
      }),
    [imageModels, loraList?.installed],
  );

  const [state, actions] = usePromptMatrix(target);

  const value = useMemo<PromptMatrixContextValue>(
    () => ({ state, actions, target }),
    [state, actions, target],
  );

  return (
    <PromptMatrixContext.Provider value={value}>
      {children}
    </PromptMatrixContext.Provider>
  );
}

/** The shared prompt matrix. Throws outside the provider — never a silent null. */
export function usePromptMatrixApp(): PromptMatrixContextValue {
  const ctx = useContext(PromptMatrixContext);
  if (ctx === null) {
    throw new Error(
      "usePromptMatrixApp must be used within a PromptMatrixProvider (mounted in App.tsx).",
    );
  }
  return ctx;
}
