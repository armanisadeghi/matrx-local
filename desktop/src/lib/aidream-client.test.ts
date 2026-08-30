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

  it("sends authentication with the organization aidream now requires", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ agents: [], count: 0 }), { status: 200 }),
    );

    await fetchAIDreamAgents("test-jwt", { organizationId: "org-123" });

    const init = fetchMock.mock.calls[0]?.[1];
    expect(init?.headers).toEqual({
      Authorization: "Bearer test-jwt",
      "X-Organization-Id": "org-123",
    });
  });

  it("refuses an authenticated GET with no organization BEFORE it ever fetches", async () => {
    // aidream's AuthMiddleware refuses every authenticated request with no
    // X-Organization-Id header (400 organization_required) before it routes.
    // The client fails closed at the SAME boundary instead of spending a
    // round trip on a guaranteed refusal — and never invents an organization.
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ agents: [], count: 0 }), { status: 200 }),
    );

    await expect(fetchAIDreamAgents("test-jwt")).rejects.toThrow(
      /organizationId/,
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
