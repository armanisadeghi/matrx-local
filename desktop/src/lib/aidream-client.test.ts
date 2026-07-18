import { beforeEach, describe, expect, it, vi } from "vitest";

const { getAIDreamServerUrl } = vi.hoisted(() => ({
  getAIDreamServerUrl: vi.fn().mockResolvedValue("https://server.example.com"),
}));

vi.mock("@/lib/app-config", () => ({ getAIDreamServerUrl }));

import { fetchAIDreamAgents, fetchAIDreamModels } from "./aidream-client";

beforeEach(() => {
  vi.restoreAllMocks();
  getAIDreamServerUrl.mockResolvedValue("https://server.example.com");
});

describe("AIDream GET requests", () => {
  it("does not force a JSON-content preflight for a bodyless public GET", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ models: [], count: 0 }), { status: 200 }),
    );

    await fetchAIDreamModels();

    const init = fetchMock.mock.calls[0]?.[1];
    expect(init?.method).toBe("GET");
    expect(init?.headers).toEqual({});
  });

  it("sends authentication without an unnecessary content-type header", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ agents: [], count: 0 }), { status: 200 }),
    );

    await fetchAIDreamAgents("test-jwt");

    const init = fetchMock.mock.calls[0]?.[1];
    expect(init?.headers).toEqual({ Authorization: "Bearer test-jwt" });
  });
});
