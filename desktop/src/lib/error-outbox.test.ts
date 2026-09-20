// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  buildDurableErrorEvent,
  createErrorOutboxIdentityCoordinator,
  createErrorOutboxController,
  enqueueDurableClientError,
  setErrorOutboxCaptureContext,
  sourceFeatureForRoute,
  uploadIdentityBoundErrorBatch,
  type DurableErrorEvent,
  type ErrorOutboxBridge,
} from "./error-outbox";

function event(id: string): DurableErrorEvent {
  return {
    id,
    schemaVersion: 1,
    occurredAt: "2026-09-15T00:00:00.000Z",
    level: "error",
    source: "test",
    message: `message-${id}`,
    route: "/test",
    windowLabel: "main",
    userId: "user-one",
    organizationId: "org-one",
  };
}

describe("durable renderer error outbox", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/settings?secret=do-not-keep#/devices?token=nope");
  });

  it("sanitizes secrets and excludes route query strings", () => {
    const built = buildDurableErrorEvent({
      level: "error",
      source: "browser-runtime",
      message: "Bearer abc123 https://x.test?a=1&token=secret sk-abcdefghijk",
    });

    expect(built.message).toContain("Bearer [REDACTED]");
    expect(built.message).toContain("token=[REDACTED]");
    expect(built.message).toContain("[REDACTED_API_KEY]");
    expect(built.route).toBe("/devices");
  });

  it("redacts structured, header, cookie, OAuth, and encoded secrets", () => {
    const built = buildDurableErrorEvent({
      level: "error",
      message:
        'Authorization: Basic dXNlcjpwYXNz {"access_token":"json-secret","password":"pw"} Cookie: session=abc code=oauth-code&state=oauth-state token%3Dencoded',
    });

    expect(built.message).not.toMatch(
      /dXNlcjpwYXNz|json-secret|\bpw\b|session=abc|oauth-code|oauth-state|encoded/,
    );
  });

  it("redacts sensitive key classes regardless of prefix or casing", () => {
    const built = buildDurableErrorEvent({
      level: "error",
      message:
        'AWS_SECRET_ACCESS_KEY=aws-value SUPABASE_SERVICE_ROLE_KEY=supa-value passwd=passwd-value credential=credential-value %43oDe=oauth-value',
    });

    expect(built.message).not.toMatch(
      /aws-value|supa-value|passwd-value|credential-value|oauth-value/,
    );
    expect(built.message.match(/\[REDACTED]/g)).toHaveLength(5);
  });

  it("keeps only a bounded hash-like causal signature", () => {
    const accepted = buildDurableErrorEvent({
      level: "error",
      message: "failure",
      causalSignature: "permission-probe:plugin:accessibility",
    });
    const rejected = buildDurableErrorEvent({
      level: "error",
      message: "failure",
      causalSignature: "token=private-value",
    });
    const repeated = buildDurableErrorEvent({
      level: "error",
      message: "another failure",
      causalSignature: "permission-probe:plugin:accessibility",
    });

    expect(accepted.causalSignature).toMatch(/^[a-f0-9]{16}$/);
    expect(repeated.causalSignature).toBe(accepted.causalSignature);
    expect(rejected.causalSignature).toBeNull();
  });

  it("refuses identity-required capture before context is available", () => {
    setErrorOutboxCaptureContext(null);
    (window as Window & {
      __TAURI_INTERNALS__?: { invoke?: unknown };
    }).__TAURI_INTERNALS__ = { invoke: vi.fn() };

    expect(
      enqueueDurableClientError({
        level: "error",
        message: "must not enqueue",
        requireIdentity: true,
      }),
    ).toBe(false);
    delete (window as Window & { __TAURI_INTERNALS__?: unknown })
      .__TAURI_INTERNALS__;
  });

  it("acknowledges only successful uploads and retains the failed suffix", async () => {
    const queued = [event("one"), event("two"), event("three")];
    const acknowledged: string[][] = [];
    const bridge: ErrorOutboxBridge = {
      enqueue: vi.fn(async (next) => {
        queued.push(next);
      }),
      read: vi.fn(async () => [...queued]),
      acknowledge: vi.fn(async (ids) => {
        acknowledged.push(ids);
      }),
    };
    const upload = vi.fn(async (events: DurableErrorEvent[]) => {
      const acknowledgedIds: string[] = [];
      for (const next of events) {
        if (next.id === "two") break;
        acknowledgedIds.push(next.id);
      }
      return acknowledgedIds;
    });
    const controller = createErrorOutboxController(bridge, () => upload);

    await controller.flush();

    expect(upload).toHaveBeenCalledWith(queued);
    expect(acknowledged).toEqual([["one"]]);
  });

  it("serializes enqueues before a flush reads the batch", async () => {
    const order: string[] = [];
    const bridge: ErrorOutboxBridge = {
      enqueue: vi.fn(async () => {
        order.push("enqueue");
      }),
      read: vi.fn(async () => {
        order.push("read");
        return [];
      }),
      acknowledge: vi.fn(async () => undefined),
    };
    const controller = createErrorOutboxController(bridge, () =>
      vi.fn(async (events: DurableErrorEvent[]) =>
        events.map(({ id }) => id),
      ),
    );

    controller.enqueue(event("one"));
    await controller.flush();

    expect(order).toEqual(["enqueue", "read"]);
  });

  it("leaves pre-login events local without starving eligible records", async () => {
    const preLogin = Array.from({ length: 25 }, (_, index) => ({
      ...event(`pre-login-${index}`),
      userId: null,
      organizationId: null,
    }));
    const signedIn = event("signed-in");
    const acknowledged: string[][] = [];
    const bridge: ErrorOutboxBridge = {
      enqueue: vi.fn(async () => undefined),
      read: vi.fn(async () => [...preLogin, signedIn]),
      acknowledge: vi.fn(async (ids) => {
        acknowledged.push(ids);
      }),
    };
    const controller = createErrorOutboxController(bridge, () => async (events) =>
      events
        .filter(
          (next) =>
            next.userId === "user-one" && next.organizationId === "org-one",
        )
        .map(({ id }) => id),
    );

    await controller.flush();

    expect(acknowledged).toEqual([["signed-in"]]);
  });

  it("clears identity synchronously and rejects stale async refresh completion", () => {
    const published: Array<{ userId: string; organizationId: string } | null> = [];
    const coordinator = createErrorOutboxIdentityCoordinator((context) => {
      published.push(context);
    });

    const staleGeneration = coordinator.beginTransition();
    expect(published).toEqual([null]);
    const currentGeneration = coordinator.beginTransition();
    expect(published).toEqual([null, null]);

    expect(
      coordinator.commit(staleGeneration, {
        userId: "old-user",
        organizationId: "old-org",
      }),
    ).toBe(false);
    expect(
      coordinator.commit(currentGeneration, {
        userId: "new-user",
        organizationId: "new-org",
      }),
    ).toBe(true);
    expect(published).toEqual([
      null,
      null,
      { userId: "new-user", organizationId: "new-org" },
    ]);
  });

  it("retains the in-flight suffix when identity changes during a pinned batch", async () => {
    let identityCurrent = true;
    const rpc = vi.fn(async () => {
      identityCurrent = false;
      return { error: null };
    });

    const acknowledged = await uploadIdentityBoundErrorBatch(
      [event("one"), event("two")],
      {
        userId: "user-one",
        organizationId: "org-one",
        accessToken: "token-for-user-one",
      },
      rpc,
      () => identityCurrent,
    );

    expect(rpc).toHaveBeenCalledTimes(1);
    expect(acknowledged).toEqual([]);
  });
});

describe("sourceFeatureForRoute", () => {
  it("maps a known route", () => {
    expect(sourceFeatureForRoute("/chat")).toBe("chat");
  });

  it("maps a nested route by its first segment", () => {
    expect(sourceFeatureForRoute("/browser/tauri")).toBe("scraper");
  });

  it("falls back to client-unmapped for an empty route", () => {
    expect(sourceFeatureForRoute("")).toBe("client-unmapped");
  });

  it("falls back to client-unmapped for an unregistered route", () => {
    expect(sourceFeatureForRoute("/not-a-real-route")).toBe("client-unmapped");
  });
});
