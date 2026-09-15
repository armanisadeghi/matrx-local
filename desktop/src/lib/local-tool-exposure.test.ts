import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ getEngineAuthHeaders: vi.fn() }));

vi.mock("@/lib/api", () => ({ engine: mocks }));

import { fetchLocalTools, setLocalToolExposure } from "./local-tool-exposure";

describe("local tool exposure engine requests", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getEngineAuthHeaders.mockResolvedValue({
      Authorization: "Bearer test-token",
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  it("authorizes the protected local tool catalog request", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ tools: [] }) });

    await expect(fetchLocalTools("http://127.0.0.1:22140")).resolves.toEqual({
      tools: [],
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:22140/chat/local-tools",
      expect.objectContaining({
        headers: { Authorization: "Bearer test-token" },
      }),
    );
  });

  it("authorizes the exposure mutation while preserving its JSON request", async () => {
    fetchMock.mockResolvedValue({ ok: true });

    await expect(
      setLocalToolExposure("http://127.0.0.1:22140", ["local_shell"]),
    ).resolves.toBeUndefined();

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:22140/chat/local-tools/exposure",
      expect.objectContaining({
        method: "PUT",
        headers: {
          Authorization: "Bearer test-token",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ disabled_tools: ["local_shell"] }),
      }),
    );
  });
});
