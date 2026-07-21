import { useSyncExternalStore } from "react";

import type { ActionNeeded, ActionNeededSnapshot } from "./types";

type Listener = () => void;

function comparableObservedAt(item: ActionNeeded): number {
  const observed = item.observed_at ?? 0;
  return observed > 0 && observed < 10_000_000_000 ? observed * 1000 : observed;
}

function isActionNeeded(value: unknown): value is ActionNeeded {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<ActionNeeded>;
  return (
    typeof item.fingerprint === "string" &&
    typeof item.code === "string" &&
    typeof item.feature === "string" &&
    typeof item.title === "string" &&
    typeof item.message === "string" &&
    typeof item.source === "string" &&
    !!item.action &&
    typeof item.action.kind === "string" &&
    typeof item.action.label === "string"
  );
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(stableJson).join(",")}]`;
  }
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableJson(record[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value) ?? "undefined";
}

function sameActionNeeded(left: ActionNeeded, right: ActionNeeded): boolean {
  return stableJson(left) === stableJson(right);
}

export class ActionNeededStore {
  private items = new Map<string, ActionNeeded>();
  private sourceVersions = new Map<string, number>();
  private sourceEpochs = new Map<string, string>();
  private backendSources = new Set<string>();
  private localSources = new Set<string>();
  private backendEpoch: string | null = null;
  private resolvedAt = new Map<string, number>();
  private resolvedSources = new Map<string, string>();
  private listeners = new Set<Listener>();
  private cached: ActionNeeded[] = [];
  private localVersion = 0;

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = (): ActionNeeded[] => this.cached;

  private publish(): void {
    this.cached = Array.from(this.items.values())
      .filter((item) => item.status !== "resolved")
      .sort(
        (a, b) =>
          comparableObservedAt(b) - comparableObservedAt(a) ||
          a.fingerprint.localeCompare(b.fingerprint),
      );
    this.listeners.forEach((listener) => listener());
  }

  upsert(item: ActionNeeded): void {
    if (!isActionNeeded(item)) return;
    if (item.status === "resolved") {
      this.resolve(item.fingerprint, item.observed_at ?? undefined);
      return;
    }
    const tombstone = this.resolvedAt.get(item.fingerprint);
    if (tombstone !== undefined && comparableObservedAt(item) <= tombstone) {
      return;
    }
    const existing = this.items.get(item.fingerprint);
    if (
      existing &&
      existing.observed_at != null &&
      item.observed_at != null &&
      item.observed_at < existing.observed_at
    ) {
      return;
    }
    if (existing && sameActionNeeded(existing, item)) return;
    this.items.set(item.fingerprint, item);
    this.resolvedSources.delete(item.fingerprint);
    this.publish();
  }

  resolve(fingerprint: string, observedAt?: number): void {
    const existing = this.items.get(fingerprint);
    if (
      existing &&
      observedAt != null &&
      existing.observed_at != null &&
      observedAt < existing.observed_at
    ) {
      return;
    }
    const resolvedVersion = comparableObservedAt({
      ...(existing ?? ({ observed_at: 0 } as ActionNeeded)),
      observed_at: observedAt ?? Date.now(),
    });
    this.resolvedAt.set(
      fingerprint,
      Math.max(this.resolvedAt.get(fingerprint) ?? 0, resolvedVersion),
    );
    if (existing) this.resolvedSources.set(fingerprint, existing.source);
    if (this.items.delete(fingerprint)) this.publish();
  }

  resolveMatching(predicate: (item: ActionNeeded) => boolean): void {
    const now = Date.now();
    let changed = false;
    for (const [fingerprint, item] of this.items) {
      if (predicate(item)) {
        this.items.delete(fingerprint);
        changed = true;
        this.resolvedAt.set(
          fingerprint,
          Math.max(this.resolvedAt.get(fingerprint) ?? 0, now),
        );
        this.resolvedSources.set(fingerprint, item.source);
      }
    }
    if (changed) this.publish();
  }

  /** Replace all items owned by one source. `null` is an explicit clear. */
  reconcile(snapshot: ActionNeededSnapshot): void {
    let changed = false;
    if (!this.localSources.has(snapshot.source)) {
      this.backendSources.add(snapshot.source);
    }
    const previousEpoch = this.sourceEpochs.get(snapshot.source);
    if (snapshot.epoch && previousEpoch && snapshot.epoch !== previousEpoch) {
      for (const [fingerprint, item] of this.items) {
        if (item.source === snapshot.source) {
          this.items.delete(fingerprint);
          changed = true;
        }
      }
      for (const [fingerprint, source] of this.resolvedSources) {
        if (source === snapshot.source) {
          this.resolvedSources.delete(fingerprint);
          this.resolvedAt.delete(fingerprint);
        }
      }
      this.sourceVersions.delete(snapshot.source);
    }
    if (snapshot.epoch) this.sourceEpochs.set(snapshot.source, snapshot.epoch);
    const previousVersion = this.sourceVersions.get(snapshot.source) ?? -1;
    if (snapshot.version < previousVersion) return;
    this.sourceVersions.set(snapshot.source, snapshot.version);

    const incoming = new Map(
      (snapshot.items ?? [])
        .filter(isActionNeeded)
        .filter((item) => item.source === snapshot.source)
        .filter((item) => item.status !== "resolved")
        .map((item) => [item.fingerprint, item]),
    );
    for (const [fingerprint, item] of this.items) {
      if (item.source === snapshot.source && !incoming.has(fingerprint)) {
        this.items.delete(fingerprint);
        this.resolvedSources.delete(fingerprint);
        changed = true;
      }
    }
    for (const [fingerprint, item] of incoming) {
      const tombstone = this.resolvedAt.get(fingerprint);
      if (tombstone !== undefined && comparableObservedAt(item) <= tombstone) {
        continue;
      }
      const existing = this.items.get(fingerprint);
      if (
        !existing ||
        existing.observed_at == null ||
        item.observed_at == null ||
        item.observed_at >= existing.observed_at
      ) {
        if (!existing || !sameActionNeeded(existing, item)) {
          this.items.set(fingerprint, item);
          this.resolvedSources.delete(fingerprint);
          changed = true;
        }
      }
    }
    if (changed) this.publish();
  }

  reconcileLocal(source: string, items: ActionNeeded[] | null): void {
    this.localSources.add(source);
    this.localVersion += 1;
    this.reconcile({ source, version: this.localVersion, items });
  }

  acceptBackendEpoch(epoch: string): void {
    if (this.backendEpoch && this.backendEpoch !== epoch) {
      let changed = false;
      for (const [fingerprint, item] of this.items) {
        if (!this.localSources.has(item.source)) {
          this.items.delete(fingerprint);
          changed = true;
        }
      }
      for (const [fingerprint, source] of this.resolvedSources) {
        if (!this.localSources.has(source)) {
          this.resolvedSources.delete(fingerprint);
          this.resolvedAt.delete(fingerprint);
        }
      }
      for (const source of this.backendSources) {
        this.sourceVersions.delete(source);
        this.sourceEpochs.delete(source);
      }
      this.backendSources.clear();
      if (changed) this.publish();
    }
    this.backendEpoch = epoch;
  }

  reset(): void {
    this.items.clear();
    this.sourceVersions.clear();
    this.sourceEpochs.clear();
    this.backendSources.clear();
    this.localSources.clear();
    this.backendEpoch = null;
    this.resolvedAt.clear();
    this.resolvedSources.clear();
    this.localVersion = 0;
    this.publish();
  }
}

export const actionNeededStore = new ActionNeededStore();

export function reportActionNeeded(
  item: ActionNeeded | null | undefined,
): void {
  if (item) actionNeededStore.upsert(item);
}

/** User dismissed a remediation card — tombstone until a newer observation arrives. */
export function dismissActionNeeded(item: ActionNeeded): void {
  actionNeededStore.resolve(item.fingerprint, item.observed_at ?? undefined);
}

export function reconcileToolActionNeeded(
  invocationKey: string,
  item: ActionNeeded | null | undefined,
): void {
  if (item) {
    actionNeededStore.upsert({
      ...item,
      details: { ...(item.details ?? {}), invocation_key: invocationKey },
    });
    return;
  }
  actionNeededStore.resolveMatching(
    (candidate) => candidate.details?.invocation_key === invocationKey,
  );
}

export function ingestActionNeededMessage(message: unknown): void {
  if (!message || typeof message !== "object") return;
  const payload = message as {
    type?: string;
    source?: string;
    version?: number;
    epoch?: string;
    items?: ActionNeeded[] | null;
    action_needed?: ActionNeeded | null;
    fingerprint?: string;
    observed_at?: number;
  };
  if (
    payload.type === "action_needed_epoch" &&
    typeof payload.epoch === "string"
  ) {
    actionNeededStore.acceptBackendEpoch(payload.epoch);
  }
  if (
    payload.type === "action_needed_snapshot" &&
    typeof payload.source === "string" &&
    typeof payload.version === "number"
  ) {
    actionNeededStore.reconcile({
      source: payload.source,
      ...(payload.epoch ? { epoch: payload.epoch } : {}),
      version: payload.version,
      items: payload.items ?? null,
    });
  }
  if (
    payload.type === "action_needed_resolved" &&
    typeof payload.fingerprint === "string"
  ) {
    actionNeededStore.resolve(payload.fingerprint, payload.observed_at);
  }
  if (
    payload.type === "action_needed" &&
    payload.action_needed === null &&
    typeof payload.source === "string"
  ) {
    actionNeededStore.reconcileLocal(payload.source, null);
  }
  if (payload.action_needed) reportActionNeeded(payload.action_needed);
}

export function useActionNeeded(): ActionNeeded[] {
  return useSyncExternalStore(
    actionNeededStore.subscribe,
    actionNeededStore.getSnapshot,
    actionNeededStore.getSnapshot,
  );
}
