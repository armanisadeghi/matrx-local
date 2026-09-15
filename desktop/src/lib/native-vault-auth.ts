/**
 * One serialized bridge from host auth lifecycle into the provider's
 * non-secret generation fence. It intentionally treats host command failure
 * as a fence failure; callers must not adopt dependent state until it settles.
 *
 * **FS-C5b: the lifecycle it bridges is now the sync daemon's, not supabase-js's.**
 * `supabase.auth.onAuthStateChange` no longer exists in this process — the client is built with
 * the `accessToken` option, which makes `supabase.auth` throw by construction — so the single
 * subscription below listens to the daemon's `session.changed` stream instead.
 *
 * **The fence keeps its exact semantics.** It never depended on supabase-js: it depends on
 * (a) a synchronous `coordinator.fence()` taken the instant a session change is observed, before
 * any listener can act on it, and (b) `session?.user.id` — the subject — to reconcile and adopt
 * against. Both survive verbatim. What changed is only where the event comes from and what a
 * "session" is: an identity (`{ user: { id, email } }`) rather than a credential, because there is
 * no credential in this process any more. Ordering is preserved the same way it was before — the
 * envelope is built and fanned out synchronously inside the callback, and the native I/O is
 * deferred to a `setTimeout(0)` — so a subscriber still stops local work before adoption runs.
 */
import {
  invalidateNativeVaultHostActor,
  reconcileNativeVaultHostActor,
} from "@/lib/sidecar";
import { engine } from "@/lib/api";
import {
  currentSession,
  getSession,
  sessionToMatrx,
  subscribeSession,
  type MatrxSession,
  type SessionSnapshot,
} from "@/lib/custodian";
import {
  NativeVaultHostAuthCoordinator,
  type EngineAlignment,
  type NativeVaultTransitionContext,
} from "@/lib/native-vault-auth-coordinator";

const coordinator = new NativeVaultHostAuthCoordinator({
  invalidate: invalidateNativeVaultHostActor,
  reconcile: reconcileNativeVaultHostActor,
}, (context) => engine.prepareSessionTransition(context));

/** The lifecycle events this app actually has, now that the daemon owns the session. */
export type CustodyAuthEvent =
  | "INITIAL_SESSION"
  | "SIGNED_IN"
  | "SIGNED_OUT"
  | "TOKEN_REFRESHED";

export interface NativeVaultHostEventEnvelope {
  readonly event: CustodyAuthEvent;
  readonly session: MatrxSession | null;
  readonly revision: number;
  readonly completion: Promise<{ accepted: boolean }>;
  /** The daemon's full honest state, so a surface can say WHY there is no session. */
  readonly snapshot: SessionSnapshot;
}

const eventSubscribers = new Set<(envelope: NativeVaultHostEventEnvelope) => void>();
let subscriptionStarted = false;
let latestEnvelope: NativeVaultHostEventEnvelope | null = null;

/** The only session-lifecycle callback. It fences synchronously and fans out immutable work. */
function ensureHostSubscription(): void {
  if (subscriptionStarted) return;
  subscriptionStarted = true;

  let previousSubject: string | null = null;
  let delivered = false;

  const deliver = (snapshot: SessionSnapshot, rotated: boolean): void => {
    const session = sessionToMatrx(snapshot);
    const subject = session?.user.id ?? null;
    // Name the transition the way the old supabase-js events named it, so every downstream
    // branch that distinguished a first sign-in from a token refresh still can.
    const event: CustodyAuthEvent = !delivered
      ? "INITIAL_SESSION"
      : subject && subject === previousSubject && rotated
        ? "TOKEN_REFRESHED"
        : subject
          ? "SIGNED_IN"
          : "SIGNED_OUT";
    delivered = true;
    previousSubject = subject;
    handleSessionChange(event, session, snapshot);
  };

  // The state as of mount, before the first stream frame — the same job
  // `INITIAL_SESSION` did.
  void getSession().then((snapshot) => deliver(snapshot, false));
  subscribeSession((snapshot, rotated) => deliver(snapshot, rotated));
}

/** Fences synchronously, then defers every piece of native I/O — unchanged from the Supabase
 *  version, because that ordering is what the fence's correctness rests on. */
function handleSessionChange(
  event: CustodyAuthEvent,
  session: MatrxSession | null,
  snapshot: SessionSnapshot,
): void {
  {
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
    const envelope: NativeVaultHostEventEnvelope = Object.freeze({ event, session, revision, completion, snapshot });
    latestEnvelope = envelope;
    for (const listener of eventSubscribers) {
      try { listener(envelope); } catch { /* one subscriber cannot block another */ }
    }
    // A subscriber normally observes this promise; retaining this handler also
    // prevents an unmounted subscriber from creating an unhandled rejection.
    void completion.catch(() => undefined);
  }
}

/** The daemon's current honest state, for a surface that must render before the stream opens. */
export function currentCustodySnapshot(): SessionSnapshot {
  return latestEnvelope?.snapshot ?? currentSession();
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
