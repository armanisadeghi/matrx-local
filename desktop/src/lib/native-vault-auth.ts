/**
 * One serialized bridge from host auth lifecycle into the provider's
 * non-secret generation fence. It intentionally treats host command failure
 * as a fence failure; callers must not adopt dependent state until it settles.
 */
import {
  invalidateNativeVaultHostActor,
  reconcileNativeVaultHostActor,
} from "@/lib/sidecar";
import { engine } from "@/lib/api";
import supabase from "@/lib/supabase";
import type { AuthChangeEvent, Session } from "@supabase/supabase-js";
import {
  NativeVaultHostAuthCoordinator,
  type EngineAlignment,
  type NativeVaultTransitionContext,
} from "@/lib/native-vault-auth-coordinator";

const coordinator = new NativeVaultHostAuthCoordinator({
  invalidate: invalidateNativeVaultHostActor,
  reconcile: reconcileNativeVaultHostActor,
}, (context) => engine.prepareSessionTransition(context));

export interface NativeVaultHostEventEnvelope {
  readonly event: AuthChangeEvent;
  readonly session: Session | null;
  readonly revision: number;
  readonly completion: Promise<{ accepted: boolean }>;
}

const eventSubscribers = new Set<(envelope: NativeVaultHostEventEnvelope) => void>();
let subscriptionStarted = false;
let latestEnvelope: NativeVaultHostEventEnvelope | null = null;

/** The only Supabase lifecycle callback. It fences synchronously and fans out immutable work. */
function ensureHostSubscription(): void {
  if (subscriptionStarted) return;
  subscriptionStarted = true;
  supabase.auth.onAuthStateChange((event, session) => {
    const revision = coordinator.fence();
    const completion = new Promise<{ accepted: boolean }>((resolve, reject) => {
      setTimeout(() => {
        void coordinator.reconcileAndAdopt(session?.user.id ?? null, undefined, revision).then(
          () => resolve({ accepted: coordinator.adoptedGeneration(session?.user.id ?? null) === revision }),
          reject,
        );
      }, 0);
    });
    // Listeners receive the envelope while the callback is still synchronous,
    // so they can stop local work before Supabase releases its own lock.
    const envelope: NativeVaultHostEventEnvelope = Object.freeze({ event, session, revision, completion });
    latestEnvelope = envelope;
    for (const listener of eventSubscribers) {
      try { listener(envelope); } catch { /* one subscriber cannot block another */ }
    }
    // A subscriber normally observes this promise; retaining this handler also
    // prevents an unmounted subscriber from creating an unhandled rejection.
    void completion.catch(() => undefined);
  });
}

export function subscribeNativeVaultHostEvents(listener: (envelope: NativeVaultHostEventEnvelope) => void): () => void {
  ensureHostSubscription();
  eventSubscribers.add(listener);
  if (latestEnvelope) {
    const replay = latestEnvelope;
    queueMicrotask(() => { if (eventSubscribers.has(listener) && latestEnvelope === replay) { try { listener(replay); } catch { /* isolated */ } } });
  }
  return () => eventSubscribers.delete(listener);
}

export { NativeVaultHostAuthCoordinator } from "@/lib/native-vault-auth-coordinator";
export type { EngineAlignment, NativeVaultTransitionContext } from "@/lib/native-vault-auth-coordinator";

export function invalidateNativeVaultBeforeHostMutation(): Promise<void> {
  return coordinator.invalidateBeforeHostMutation();
}

/** Call synchronously inside an auth callback; do no native I/O there. */
export function fenceNativeVaultHostAdoption(subject?: string | null): number {
  return coordinator.fence(subject);
}

export function reconcileNativeVaultAfterHostSession(subject: string | null, generation?: number): Promise<void> {
  return coordinator.reconcileAndAdopt(subject, undefined, generation).then(() => undefined);
}

export function reconcileNativeVaultAndAdopt<T>(
  subject: string | null,
  adopt: () => Promise<T> | T,
  generation?: number,
): Promise<T | undefined> {
  return coordinator.reconcileAndAdopt(subject, adopt, generation);
}

/** Token and engine paths must not use a session observed before reconciliation. */
export function isNativeVaultHostSessionAdopted(subject: string | null | undefined): boolean {
  return coordinator.isAdopted(subject);
}

/** A background task snapshots this and rechecks it immediately before I/O. */
export function nativeVaultAdoptedHostGeneration(subject: string | null | undefined): number | null {
  return coordinator.adoptedGeneration(subject);
}

/** Engine/background callers must hold an origin-bound current alignment. */
export function nativeVaultEngineTransitionContext(subject: string | null | undefined): NativeVaultTransitionContext | null {
  return coordinator.engineContext(subject);
}

export function nativeVaultEngineAlignment(): EngineAlignment | null {
  return coordinator.engineAlignment();
}

/** Engine discovery reuses the current host revision; it never promotes an old callback. */
export function alignNativeVaultEngineForCurrentSubject(subject: string | null): Promise<EngineAlignment | null> {
  return coordinator.alignEngineForAdopted(subject);
}

/** Retry uses the current coordinator authority, never a retained auth callback. */
export function retryNativeVaultAccountCleanup(subject: string): Promise<EngineAlignment | null> {
  return coordinator.alignEngineForAdopted(subject);
}
