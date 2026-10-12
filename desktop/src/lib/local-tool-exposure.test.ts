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

  const sent = () => {
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    return { url, init, headers: new Headers(init.headers) };
  };

  it("authorizes the protected local tool catalog request", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ tools: [] }), {
        headers: { "content-type": "application/json" },
      }),
    );

    await expect(fetchLocalTools("http://127.0.0.1:22140")).resolves.toEqual({
      tools: [],
    });

    const { url, headers } = sent();
    expect(url).toBe("http://127.0.0.1:22140/chat/local-tools");
    expect(headers.get("authorization")).toBe("Bearer test-token");
  });

  it("authorizes the exposure mutation while preserving its JSON request", async () => {
    fetchMock.mockResolvedValue(
      new Response("{}", { headers: { "content-type": "application/json" } }),
    );

    await expect(
      setLocalToolExposure("http://127.0.0.1:22140", ["local_shell"]),
    ).resolves.toBeUndefined();

    const { url, init, headers } = sent();
    expect(url).toBe("http://127.0.0.1:22140/chat/local-tools/exposure");
    expect(init.method).toBe("PUT");
    expect(headers.get("authorization")).toBe("Bearer test-token");
    expect(headers.get("content-type")).toContain("application/json");
    expect(init.body).toBe(JSON.stringify({ disabled_tools: ["local_shell"] }));
  });

  it("a refusal rejects", async () => {
    fetchMock.mockResolvedValue(new Response("{}", { status: 500 }));
    await expect(fetchLocalTools("http://127.0.0.1:22140")).rejects.toThrow();
  });
});
