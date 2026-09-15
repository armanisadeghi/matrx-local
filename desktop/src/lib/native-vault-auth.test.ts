import { expect, it, vi } from "vitest";
import { NativeVaultHostAuthCoordinator } from "./native-vault-auth";
const unavailable = async () => ({ status: "unavailable" as const });

it("does not adopt dependent state when the native command is missing or rejects", async () => {
  const adopt = vi.fn();
  const missing = new NativeVaultHostAuthCoordinator({
    invalidate: async () => "applied",
    reconcile: async () => null,
  }, unavailable);
  await expect(missing.reconcileAndAdopt("11111111-1111-4111-8111-111111111111", adopt)).rejects.toThrow("Retry");
  expect(adopt).not.toHaveBeenCalled();
  expect(missing.isAdopted("11111111-1111-4111-8111-111111111111")).toBe(false);

  const rejected = new NativeVaultHostAuthCoordinator({
    invalidate: async () => "state_corrupt",
    reconcile: async () => "busy",
  }, unavailable);
  await expect(rejected.invalidateBeforeHostMutation()).rejects.toThrow("Retry");
  await expect(rejected.reconcileAndAdopt("11111111-1111-4111-8111-111111111111", adopt)).rejects.toThrow("Retry");
  expect(adopt).not.toHaveBeenCalled();
});

it("serializes an invalidation before a later actor can be adopted", async () => {
  const calls: string[] = [];
  const blocked = new Promise<void>(() => undefined);
  const coordinator = new NativeVaultHostAuthCoordinator({
    invalidate: async () => { calls.push("invalidate"); await blocked; return "applied"; },
    reconcile: async (subject) => { calls.push(`reconcile:${subject}`); return "applied"; },
  }, unavailable);
  const invalidation = coordinator.invalidateBeforeHostMutation().catch(() => undefined);
  const adoption = coordinator.reconcileAndAdopt("22222222-2222-4222-8222-222222222222", () => calls.push("adopt"));
  await Promise.resolve();
  expect(calls).toEqual([]);
  await Promise.all([invalidation, adoption]);
  expect(calls).toEqual(["reconcile:22222222-2222-4222-8222-222222222222", "adopt"]);
  expect(coordinator.isAdopted("22222222-2222-4222-8222-222222222222")).toBe(true);
});

it("accepts same-actor unchanged reconciliation without a new invalidation", async () => {
  const reconcile = vi.fn(async () => "unchanged" as const);
  const coordinator = new NativeVaultHostAuthCoordinator({ invalidate: async () => "applied", reconcile }, unavailable);
  await coordinator.reconcileAndAdopt("33333333-3333-4333-8333-333333333333");
  await coordinator.reconcileAndAdopt("33333333-3333-4333-8333-333333333333");
  expect(reconcile).toHaveBeenCalledTimes(2);
  expect(coordinator.isAdopted("33333333-3333-4333-8333-333333333333")).toBe(true);
});

it("drops an older deferred reconciliation after a newer auth event fences it", async () => {
  let releaseOld!: () => void;
  const oldPending = new Promise<void>((resolve) => { releaseOld = resolve; });
  const adopted = vi.fn();
  const coordinator = new NativeVaultHostAuthCoordinator({
    invalidate: async () => "applied",
    reconcile: async (subject) => {
      if (subject === "old") await oldPending;
      return "applied";
    },
  }, unavailable);
  const oldFence = coordinator.fence();
  const old = coordinator.reconcileAndAdopt("old", adopted, oldFence);
  await Promise.resolve();
  const newFence = coordinator.fence();
  const fresh = coordinator.reconcileAndAdopt("new", adopted, newFence);
  releaseOld();
  await Promise.all([old, fresh]);
  expect(adopted).toHaveBeenCalledTimes(1);
  expect(coordinator.isAdopted("old")).toBe(false);
  expect(coordinator.isAdopted("new")).toBe(true);
});

it("revokes the prior actor synchronously when mutation is queued", async () => {
  let release!: () => void;
  const wait = new Promise<void>((resolve) => { release = resolve; });
  const coordinator = new NativeVaultHostAuthCoordinator({
    invalidate: async () => { await wait; return "applied"; },
    reconcile: async () => "applied",
  }, unavailable);
  await coordinator.reconcileAndAdopt("current");
  const invalidation = coordinator.invalidateBeforeHostMutation();
  expect(coordinator.isAdopted("current")).toBe(false);
  release();
  await invalidation;
});

it("gives consecutive lifecycle events distinct revisions", async () => {
  const coordinator = new NativeVaultHostAuthCoordinator({
    invalidate: async () => "applied",
    reconcile: async () => "applied",
  }, unavailable);
  const first = coordinator.fence("actor-b");
  const second = coordinator.fence("actor-b");
  expect(second).toBeGreaterThan(first);
  await coordinator.reconcileAndAdopt("actor-b", undefined, second);
  expect(coordinator.isAdopted("actor-b")).toBe(true);
});

it("adopts an accepted anonymous host state while keeping engine authority fenced", async () => {
  const coordinator = new NativeVaultHostAuthCoordinator({
    invalidate: async () => "applied",
    reconcile: async () => "applied",
  }, unavailable);
  const revision = coordinator.fence(null);
  await coordinator.reconcileAndAdopt(null, undefined, revision);
  expect(coordinator.isAdopted(undefined)).toBe(false);
  expect(coordinator.isAdopted(null)).toBe(true);
  expect(coordinator.adoptedGeneration(null)).toBe(revision);
  expect(coordinator.engineContext(null)).toBeNull();
});

it("drops a deferred same-actor transition when E2 fences E1", async () => {
  let releaseFirst!: () => void;
  const firstPending = new Promise<void>((resolve) => { releaseFirst = resolve; });
  let calls = 0;
  const adopt = vi.fn();
  const coordinator = new NativeVaultHostAuthCoordinator({
    invalidate: async () => "applied",
    reconcile: async () => { calls += 1; if (calls === 1) await firstPending; return "applied"; },
  }, unavailable);
  const e1 = coordinator.reconcileAndAdopt("44444444-4444-4444-8444-444444444444", adopt, coordinator.fence());
  await Promise.resolve();
  const e2 = coordinator.reconcileAndAdopt("44444444-4444-4444-8444-444444444444", adopt, coordinator.fence());
  releaseFirst();
  await Promise.all([e1, e2]);
  expect(adopt).toHaveBeenCalledOnce();
  expect(coordinator.isAdopted("44444444-4444-4444-8444-444444444444")).toBe(true);
});

it("never publishes an explicit engine supersession", async () => {
  const adopt = vi.fn();
  const coordinator = new NativeVaultHostAuthCoordinator({ invalidate: async () => "applied", reconcile: async () => "applied" }, async () => ({ status: "superseded" }));
  await expect(coordinator.invalidateBeforeHostMutation()).rejects.toThrow("no longer current");
  await expect(coordinator.reconcileAndAdopt("55555555-5555-4555-8555-555555555555", adopt)).resolves.toBeUndefined();
  expect(adopt).not.toHaveBeenCalled();
  expect(coordinator.isAdopted("55555555-5555-4555-8555-555555555555")).toBe(false);
  expect(await coordinator.alignEngineForAdopted("55555555-5555-4555-8555-555555555555")).toBeNull();
});

it("removes prior engine authority on current realignment supersession while retaining host adoption", async () => {
  let calls = 0;
  const subject = "77777777-7777-4777-8777-777777777777";
  const coordinator = new NativeVaultHostAuthCoordinator({ invalidate: async () => "applied", reconcile: async () => "applied" }, async () => {
    calls += 1;
    return calls === 1
      ? { status: "aligned" as const, origin: "http://engine.test", generation: "generation", credentialRevision: 1, subject }
      : { status: "superseded" as const };
  });
  await coordinator.reconcileAndAdopt(subject);
  expect(coordinator.engineContext(subject)).not.toBeNull();
  await expect(coordinator.alignEngineForAdopted(subject)).resolves.toBeNull();
  expect(coordinator.isAdopted(subject)).toBe(true);
  expect(coordinator.engineContext(subject)).toBeNull();
});

it("removes a prior same-revision adoption when reconciliation is explicitly superseded", async () => {
  let calls = 0;
  const subject = "88888888-8888-4888-8888-888888888888";
  const coordinator = new NativeVaultHostAuthCoordinator({ invalidate: async () => "applied", reconcile: async () => "applied" }, async () => {
    calls += 1;
    return calls === 1 ? { status: "unavailable" as const } : { status: "superseded" as const };
  });
  const revision = coordinator.fence();
  await coordinator.reconcileAndAdopt(subject, undefined, revision);
  expect(coordinator.isAdopted(subject)).toBe(true);
  await coordinator.reconcileAndAdopt(subject, undefined, revision);
  expect(coordinator.isAdopted(subject)).toBe(false);
});

it("times out the caller while retaining the hung native operation in the serialized queue", async () => {
  vi.useFakeTimers();
  let release!: () => void;
  const hung = new Promise<void>((resolve) => { release = resolve; });
  const reconcile = vi.fn(async () => "applied" as const);
  const coordinator = new NativeVaultHostAuthCoordinator({
    invalidate: async () => { await hung; return "applied" as const; },
    reconcile,
  }, unavailable);
  const timedOut = coordinator.invalidateBeforeHostMutation();
  void timedOut.catch(() => undefined);
  await vi.advanceTimersByTimeAsync(5_001);
  await expect(timedOut).rejects.toThrow("timed out");
  const later = coordinator.reconcileAndAdopt("actor-b");
  await Promise.resolve();
  expect(reconcile).not.toHaveBeenCalled();
  release();
  await later;
  expect(reconcile).toHaveBeenCalledOnce();
  vi.useRealTimers();
});
