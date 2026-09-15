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
  getAuthedSession,
  getToken,
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
let engineAlignmentInFlight: { subject: string; revision: number; promise: Promise<EngineAlignment | null> } | null = null;

function publishHostReconciliation(event: CustodyAuthEvent, session: MatrxSession | null, snapshot: SessionSnapshot): NativeVaultHostEventEnvelope {
  const revision = coordinator.fence();
  const completion = new Promise<{ accepted: boolean }>((resolve, reject) => {
    setTimeout(() => void coordinator.reconcileAndAdopt(session?.user.id ?? null, undefined, revision).then(
      () => resolve({ accepted: coordinator.adoptedGeneration(session?.user.id ?? null) === revision }), reject,
    ), 0);
  });
  const envelope: NativeVaultHostEventEnvelope = Object.freeze({ event, session, revision, completion, snapshot });
  latestEnvelope = envelope;
  for (const listener of eventSubscribers) { try { listener(envelope); } catch { /* isolated */ } }
  void completion.catch(() => undefined);
  return envelope;
}

/** The only session-lifecycle callback. It fences synchronously and fans out immutable work. */
function ensureHostSubscription(): void {
  if (subscriptionStarted) return;
  subscriptionStarted = true;
  let previousSubject: string | null = null;
  let delivered = false;
  const deliver = (snapshot: SessionSnapshot, rotated: boolean): void => {
    const session = sessionToMatrx(snapshot);
    const subject = session?.user.id ?? null;
    const event: CustodyAuthEvent = !delivered ? "INITIAL_SESSION" : subject && subject === previousSubject && rotated ? "TOKEN_REFRESHED" : subject ? "SIGNED_IN" : "SIGNED_OUT";
    delivered = true;
    previousSubject = subject;
    publishHostReconciliation(event, session, snapshot);
  };
  void getSession().then((snapshot) => deliver(snapshot, false));
  subscribeSession((snapshot, rotated) => deliver(snapshot, rotated));
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

export function isNativeVaultHostRevisionCurrent(revision: number): boolean { return coordinator.isCurrentRevision(revision); }

/**
 * THE AUTH-READINESS RULE — "signed out" is an answer the daemon gives, never a
 * race this app loses.
 *
 * Measured 2026-09-15 on installed 1.4.115 (verify-2026-09-15-matrx-local.md):
 * a page that mounted during app start sent all five of its reads before the
 * daemon's first session answer had landed, `getAuthedSession()` answered null,
 * `authHeaders()` attached nothing, the engine correctly answered 401 on every
 * one, and the screen said "Couldn't read your conversations" on a Mac that was
 * signed in the whole time. Nothing re-asked.
 *
 * So no consumer is told "no token" until the daemon's state is KNOWN. This
 * resolves once the first custody envelope has been delivered and adopted, or
 * once the bound expires — never rejects: an unreachable daemon is a
 * determinate answer of its own (DAEMON_DOWN), not a reason to hang a request.
 */
const CUSTODY_SETTLE_TIMEOUT_MS = 8_000;
const CUSTODY_SETTLE_POLL_MS = 25;

export async function waitForCustodySettlement(
  timeoutMs: number = CUSTODY_SETTLE_TIMEOUT_MS,
): Promise<boolean> {
  ensureHostSubscription();
  const deadline = Date.now() + timeoutMs;
  while (latestEnvelope === null && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, CUSTODY_SETTLE_POLL_MS));
  }
  const envelope = latestEnvelope;
  if (!envelope) return false;
  await envelope.completion.catch(() => undefined);
  return true;
}

/** How long a signed-in daemon gets to produce the token its snapshot implies. */
const GRANT_LAG_TIMEOUT_MS = 3_000;

/**
 * The daemon's session, allowing for its token lagging its own snapshot.
 *
 * `null` means one thing only: the daemon says nobody is signed in. A signed-in
 * daemon that has not produced a grant yet is a WAIT, never a "no" — answering
 * "no" there is what sent five unauthenticated Coding Sessions reads at mount.
 * If the grant never lands, the caller is told that, in those words; it is never
 * dressed up as being signed out.
 */
async function observeDaemonSession(): Promise<
  Awaited<ReturnType<typeof getAuthedSession>>
> {
  const deadline = Date.now() + GRANT_LAG_TIMEOUT_MS;
  let wait = CUSTODY_SETTLE_POLL_MS;
  for (;;) {
    const observed = await getAuthedSession();
    if (observed) return observed;
    if (!currentCustodySnapshot().signed_in) return null;
    if (Date.now() >= deadline) {
      throw new Error(
        "You are signed in, but the AI Matrx session daemon on this Mac has not " +
          "handed over a session yet. It retries on its own; if this does not " +
          "clear within a minute, choose Start sync in Matrx Local.",
      );
    }
    await new Promise((resolve) => setTimeout(resolve, wait));
    wait = Math.min(wait * 2, 400);
  }
}

/** Wait for daemon identity, the current envelope, and the engine fence before releasing a token. */
export async function resolveNativeVaultEngineAccessToken(): Promise<string | null> {
  ensureHostSubscription();
  await waitForCustodySettlement();
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const observed = await observeDaemonSession();
    if (!observed) return null;
    const envelope = latestEnvelope;
    if (envelope) await envelope.completion;
    if (envelope !== latestEnvelope) continue;
    let context = nativeVaultEngineTransitionContext(observed.user.id);
    // Initial host adoption may run before engine discovery and deliberately
    // records `unavailable`. Once a consumer reaches a discovered engine, one
    // serialized alignment is allowed to establish its origin-bound context.
    if (!context && coordinator.isAdopted(observed.user.id)) {
      const revision = nativeVaultAdoptedHostGeneration(observed.user.id);
      if (revision === null) continue;
      if (!engineAlignmentInFlight || engineAlignmentInFlight.subject !== observed.user.id || engineAlignmentInFlight.revision !== revision) {
        const promise = coordinator.alignEngineForAdopted(observed.user.id).finally(() => {
          if (engineAlignmentInFlight?.promise === promise) engineAlignmentInFlight = null;
        });
        engineAlignmentInFlight = { subject: observed.user.id, revision, promise };
      }
      await engineAlignmentInFlight.promise;
      context = nativeVaultEngineTransitionContext(observed.user.id);
    }
    if (!context) throw new Error("Your account is signed in, but its connection to this engine needs recovery. Retry account connection.");
    const token = await getToken();
    const current = await getAuthedSession();
    if (!token || !current || current.user.id !== observed.user.id || envelope !== latestEnvelope || !context.isCurrent()) continue;
    return token;
  }
  throw new Error("Your account connection changed while this request was waiting. Retry account connection.");
}

/** Engine discovery reuses the current host revision; it never promotes an old callback. */
export function alignNativeVaultEngineForCurrentSubject(subject: string | null): Promise<EngineAlignment | null> {
  return coordinator.alignEngineForAdopted(subject);
}

/** Retry publishes a new daemon lifecycle envelope; a rejected envelope cannot be re-adopted. */
export async function retryNativeVaultAccountCleanup(subject: string | null): Promise<boolean> {
  ensureHostSubscription();
  const snapshot = await getSession();
  const session = sessionToMatrx(snapshot);
  if ((session?.user.id ?? null) !== subject) return false;
  const envelope = publishHostReconciliation("TOKEN_REFRESHED", session, snapshot);
  try {
    const { accepted } = await envelope.completion;
    const current = await getSession();
    return accepted && latestEnvelope === envelope && isNativeVaultHostRevisionCurrent(envelope.revision) && (current.user_id ?? null) === subject;
  } catch { return false; }
}
