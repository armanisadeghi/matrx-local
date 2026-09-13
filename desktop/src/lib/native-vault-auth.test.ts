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

it("settles the current signed-out revision without manufacturing an adopted actor", async () => {
  const coordinator = new NativeVaultHostAuthCoordinator({
    invalidate: async () => "applied",
    reconcile: async () => "applied",
  }, unavailable);
  const revision = coordinator.fence(null);
  await coordinator.reconcileAndAdopt(null, undefined, revision);
  expect(coordinator.isCurrentRevision(revision)).toBe(true);
  expect(coordinator.adoptedGeneration(null)).toBeNull();
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
