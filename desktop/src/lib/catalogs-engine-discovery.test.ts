// A catalog read at launch waits for engine discovery (lib/catalogs.ts): the LLM and Whisper overlays ran in the
// same tick as the first discovery, read a null engine URL, and every launch filed "engine not connected"
// warnings while the engine was coming up (2026-11-16). An engine that never appears still falls back.
import { afterEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ url: null as string | null, healAfter: 1, heals: 0 }));

vi.mock("@/lib/api", () => ({
  engine: {
    get engineUrl() {
      return state.url;
    },
    api: {},
    rediscover: async () => {
      state.heals += 1;
      if (state.healAfter > 0 && state.heals >= state.healAfter) state.url = "http://127.0.0.1:22140";
      return state.url;
    },
  },
}));
vi.mock("@ai-matrx/api/apps", () => ({
  getCatalogKind: async () => ({ kind: "llm_model", entries: [{ key: "m", payload: { name: "m" } }] }),
}));

import { fetchCatalog } from "./catalogs";

afterEach(() => {
  state.url = null;
  state.heals = 0;
  state.healAfter = 1;
  vi.useRealTimers();
});

describe("fetchCatalog before the engine is discovered", () => {
  it("heals discovery and reads the catalog instead of failing at launch", async () => {
    const entries = await fetchCatalog("llm_model");
    expect(entries).toHaveLength(1);
    expect(state.heals).toBe(1);
  });

  it("waits through a discovery another caller is still running", async () => {
    vi.useFakeTimers();
    state.healAfter = 3;
    const read = fetchCatalog("whisper_model");
    await vi.advanceTimersByTimeAsync(1000);
    await expect(read).resolves.toHaveLength(1);
  });

  it("an engine that never appears still falls back, bounded", async () => {
    vi.useFakeTimers();
    state.healAfter = 0;
    const read = fetchCatalog("llm_model");
    const settled = expect(read).rejects.toThrow("engine not connected");
    await vi.advanceTimersByTimeAsync(6000);
    await settled;
  });
});
