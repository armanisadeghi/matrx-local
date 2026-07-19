import { describe, expect, it } from "vitest";

import {
  ActionNeededStore,
  actionNeededStore,
  ingestActionNeededMessage,
  reconcileToolActionNeeded,
} from "./store";
import type { ActionNeeded } from "./types";

function item(
  fingerprint: string,
  observedAt: number,
  source = "test",
): ActionNeeded {
  return {
    fingerprint,
    code: "missing_grant",
    kind: "os_permission",
    feature: "capture",
    title: "Permission needed",
    message: "Allow capture to continue.",
    action: { kind: "navigate", label: "Open", route: "/settings" },
    source,
    status: "active",
    observed_at: observedAt,
  };
}

describe("ActionNeededStore", () => {
  it("deduplicates by fingerprint and ignores an older observation", () => {
    const store = new ActionNeededStore();
    store.upsert(item("same", 20));
    store.upsert({ ...item("same", 10), title: "stale" });
    expect(store.getSnapshot()).toHaveLength(1);
    expect(store.getSnapshot()[0]?.title).toBe("Permission needed");

    store.upsert({ ...item("same", 30), title: "new" });
    expect(store.getSnapshot()[0]?.title).toBe("new");
  });

  it("resolves a keyed item without affecting other sources", () => {
    const store = new ActionNeededStore();
    store.upsert(item("one", 1, "one-source"));
    store.upsert(item("two", 1, "two-source"));
    store.resolve("one");
    expect(store.getSnapshot().map((entry) => entry.fingerprint)).toEqual(["two"]);
  });

  it("does not let a stale resolved observation clear a newer active item", () => {
    const store = new ActionNeededStore();
    store.upsert(item("one", 10));
    store.upsert({ ...item("one", 9), status: "resolved" });
    expect(store.getSnapshot().map((entry) => entry.fingerprint)).toEqual(["one"]);
    store.upsert({ ...item("one", 11), status: "resolved" });
    expect(store.getSnapshot()).toEqual([]);
  });

  it("keeps a resolved tombstone against a delayed older upsert", () => {
    const store = new ActionNeededStore();
    store.upsert(item("late", 10));
    store.resolve("late", 20);
    store.upsert(item("late", 10));
    expect(store.getSnapshot()).toEqual([]);
    store.upsert(item("late", 21));
    expect(store.getSnapshot().map((entry) => entry.fingerprint)).toEqual(["late"]);
  });

  it("treats a null snapshot as an explicit source clear", () => {
    const store = new ActionNeededStore();
    store.reconcile({ source: "one-source", version: 1, items: [item("one", 1, "one-source")] });
    store.reconcile({ source: "two-source", version: 1, items: [item("two", 1, "two-source")] });
    store.reconcile({ source: "one-source", version: 2, items: null });
    expect(store.getSnapshot().map((entry) => entry.fingerprint)).toEqual(["two"]);
  });

  it("rejects stale source snapshots so reconnect races cannot resurrect state", () => {
    const store = new ActionNeededStore();
    store.reconcile({ source: "test", version: 4, items: null });
    store.reconcile({ source: "test", version: 3, items: [item("stale", 99)] });
    expect(store.getSnapshot()).toEqual([]);
  });

  it("accepts a restarted source's lower versions and drops its old tombstones", () => {
    const store = new ActionNeededStore();
    store.reconcile({
      source: "engine",
      epoch: "old-process",
      version: 20,
      items: [item("recurs", 100, "engine")],
    });
    store.resolve("recurs", 200);
    store.reconcile({
      source: "engine",
      epoch: "new-process",
      version: 1,
      items: [item("recurs", 1, "engine")],
    });
    expect(store.getSnapshot().map((entry) => entry.fingerprint)).toEqual(["recurs"]);
  });

  it("clears old backend sources when a restarted registry is empty", () => {
    const store = new ActionNeededStore();
    store.acceptBackendEpoch("old");
    store.reconcile({ source: "source-a", epoch: "old", version: 3, items: [item("old", 3, "source-a")] });
    store.acceptBackendEpoch("new");
    expect(store.getSnapshot()).toEqual([]);
  });

  it("clears source A when the restarted registry only publishes source B", () => {
    const store = new ActionNeededStore();
    store.acceptBackendEpoch("old");
    store.reconcile({ source: "source-a", epoch: "old", version: 3, items: [item("old", 3, "source-a")] });
    store.acceptBackendEpoch("new");
    store.reconcile({ source: "source-b", epoch: "new", version: 1, items: [item("new", 1, "source-b")] });
    expect(store.getSnapshot().map((entry) => entry.fingerprint)).toEqual(["new"]);
  });

  it("ingests WebSocket upsert, resolve, and null-clear events", () => {
    actionNeededStore.reset();
    ingestActionNeededMessage({ action_needed: item("ws", 1, "socket") });
    expect(actionNeededStore.getSnapshot()).toHaveLength(1);
    ingestActionNeededMessage({
      type: "action_needed_resolved",
      fingerprint: "ws",
    });
    expect(actionNeededStore.getSnapshot()).toEqual([]);

    ingestActionNeededMessage({ action_needed: item("ws-2", 2, "socket") });
    ingestActionNeededMessage({
      type: "action_needed",
      source: "socket",
      action_needed: null,
    });
    expect(actionNeededStore.getSnapshot()).toEqual([]);
    actionNeededStore.reset();
  });

  it("clears a prior tool requirement when that tool later succeeds", () => {
    actionNeededStore.reset();
    reconcileToolActionNeeded("Search:{}", item("tool-action", 10, "tools.network"));
    expect(actionNeededStore.getSnapshot()).toHaveLength(1);
    reconcileToolActionNeeded("Search:{}", null);
    expect(actionNeededStore.getSnapshot()).toEqual([]);
    actionNeededStore.reset();
  });

  it("does not clear a different target from the same tool", () => {
    actionNeededStore.reset();
    reconcileToolActionNeeded("Read:{path:a}", item("path-a", 10, "tools.file"));
    reconcileToolActionNeeded("Read:{path:b}", item("path-b", 10, "tools.file"));
    reconcileToolActionNeeded("Read:{path:a}", null);
    expect(actionNeededStore.getSnapshot().map((entry) => entry.fingerprint)).toEqual(["path-b"]);
    actionNeededStore.reset();
  });
});
