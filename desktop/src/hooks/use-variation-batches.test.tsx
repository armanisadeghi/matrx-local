/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  VariationBatch,
  VariationItem,
} from "@/lib/variation-batches/types";

const testState = vi.hoisted(() => {
  interface PendingWrite {
    batches: VariationBatch[];
    resolve: (value: { batches: VariationBatch[] }) => void;
  }
  const initialItem: VariationItem = {
    id: "item-1",
    prompt: "Original item",
    negativePrompt: "",
    status: "done",
    error: "",
    updatedAt: 1,
  };
  const initialBatch: VariationBatch = {
    id: "batch-1",
    name: "Original batch",
    sourcePromptId: null,
    templatePrompt: "Original template",
    templateNegative: "",
    variableListByName: {},
    items: [initialItem],
    createdAt: 1,
    updatedAt: 1,
  };
  const pendingWrites: PendingWrite[] = [];
  const put = vi.fn(
    async (_baseUrl: string, batches: VariationBatch[]) =>
      new Promise<{ batches: VariationBatch[] }>((resolve) => {
        pendingWrites.push({ batches, resolve });
      }),
  );
  return { initialBatch, pendingWrites, put };
});

vi.mock("@/lib/api", () => ({
  engine: { engineUrl: "http://engine.test" },
  getPromptMatrixPaths: vi.fn(async () => ({ variationBatches: null })),
  getPromptMatrixVariationBatches: vi.fn(async () => ({
    batches: [testState.initialBatch],
  })),
  putPromptMatrixVariationBatches: testState.put,
}));

import {
  useVariationBatches,
  type VariationBatchesActions,
  type VariationBatchesState,
} from "./use-variation-batches";

let latestActions: VariationBatchesActions | null = null;
let latestState: VariationBatchesState | null = null;

function Harness() {
  const [state, actions] = useVariationBatches();
  latestState = state;
  latestActions = actions;
  return null;
}

function currentState(): VariationBatchesState {
  if (latestState === null) throw new Error("Hook state is not ready");
  return latestState;
}

describe("useVariationBatches write serialization", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(async () => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
      .IS_REACT_ACT_ENVIRONMENT = true;
    vi.clearAllMocks();
    testState.pendingWrites.splice(0);
    latestActions = null;
    latestState = null;
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    await act(async () => {
      root.render(<Harness />);
      await Promise.resolve();
    });
    expect(currentState().ready).toBe(true);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  it("merges template and item edits through one ordered whole-file queue", async () => {
    let templateSave!: Promise<boolean>;
    let itemSave!: Promise<boolean>;
    await act(async () => {
      templateSave = latestActions!.updateBatch("batch-1", {
        name: "Two words ",
      });
      itemSave = latestActions!.updateVariationItem("batch-1", "item-1", {
        prompt: "Edited item",
      });
      await Promise.resolve();
    });

    expect(testState.pendingWrites).toHaveLength(1);
    expect(testState.pendingWrites[0]!.batches[0]!.name).toBe("Two words ");
    expect(testState.pendingWrites[0]!.batches[0]!.items[0]!.prompt).toBe(
      "Original item",
    );

    await act(async () => {
      const first = testState.pendingWrites.shift()!;
      first.resolve({ batches: first.batches });
      await templateSave;
      await Promise.resolve();
    });

    expect(testState.pendingWrites).toHaveLength(1);
    expect(testState.pendingWrites[0]!.batches[0]!.name).toBe("Two words ");
    expect(testState.pendingWrites[0]!.batches[0]!.items[0]!.prompt).toBe(
      "Edited item",
    );

    await act(async () => {
      const second = testState.pendingWrites.shift()!;
      second.resolve({ batches: second.batches });
      await itemSave;
    });

    expect(currentState().batches[0]?.name).toBe("Two words ");
    expect(currentState().batches[0]?.items[0]?.prompt).toBe("Edited item");
  });

  it("waits for an in-flight edit before replacing items during generation", async () => {
    let editSave!: Promise<boolean>;
    let generation!: Promise<{ ok: boolean; errors: string[] }>;
    await act(async () => {
      editSave = latestActions!.updateBatch("batch-1", {
        name: "Edited batch",
      });
      generation = latestActions!.generateVariations({
        batchId: "batch-1",
        sourcePromptId: null,
        templatePrompt: "Generated prompt",
        templateNegative: "",
        variableListByName: {},
        variables: [],
      });
      await Promise.resolve();
    });

    expect(testState.pendingWrites).toHaveLength(1);
    expect(testState.pendingWrites[0]!.batches[0]!.items[0]!.prompt).toBe(
      "Original item",
    );

    await act(async () => {
      const first = testState.pendingWrites.shift()!;
      first.resolve({ batches: first.batches });
      await editSave;
      await Promise.resolve();
    });

    expect(testState.pendingWrites).toHaveLength(1);
    expect(testState.pendingWrites[0]!.batches[0]!.name).toBe("Edited batch");
    expect(testState.pendingWrites[0]!.batches[0]!.items).toHaveLength(1);
    expect(testState.pendingWrites[0]!.batches[0]!.items[0]!.prompt).toBe(
      "Generated prompt",
    );

    await act(async () => {
      const second = testState.pendingWrites.shift()!;
      second.resolve({ batches: second.batches });
      await expect(generation).resolves.toEqual({ ok: true, errors: [] });
    });
  });
});
