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

    // A REAL organization id: since @ai-matrx/agents 0.6.0 the package's
    // org-context kernel writes this header AND validates the id, so the old
    // "org-123" placeholder is no longer a representative fixture.
    await fetchAIDreamAgents("test-jwt", {
      organizationId: "3f2a91c4-5b6d-4e7f-8a90-1b2c3d4e5f60",
    });

    const init = fetchMock.mock.calls[0]?.[1];
    expect(init?.headers).toEqual({
      Authorization: "Bearer test-jwt",
      "X-Organization-Id": "3f2a91c4-5b6d-4e7f-8a90-1b2c3d4e5f60",
    });
  });

  it("refuses a MALFORMED organization id before the wire instead of sending it", async () => {
    // The kernel validates what it binds (@ai-matrx/agents 0.6.0). A corrupt
    // stored id used to be sent anyway and earned an opaque server 400; now it
    // is refused here, with a message that names the problem.
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ agents: [], count: 0 }), { status: 200 }),
    );

    await expect(
      fetchAIDreamAgents("test-jwt", { organizationId: "org-123" }),
    ).rejects.toThrow(/organization ID is invalid/i);
    expect(fetchMock).not.toHaveBeenCalled();
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
