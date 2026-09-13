/**
 * One serialized bridge from host auth lifecycle into the provider's
 * non-secret generation fence. It intentionally treats host command failure
 * as a fence failure; callers must not adopt dependent state until it settles.
 */
import {
  invalidateNativeVaultHostActor,
  reconcileNativeVaultHostActor,
  type NativeVaultTransition,
} from "@/lib/sidecar";

type NativeVaultCommands = {
  invalidate(): Promise<NativeVaultTransition | null>;
  reconcile(subject: string | null): Promise<NativeVaultTransition | null>;
};

const accepted = new Set<NativeVaultTransition>([
  "applied",
  "unchanged",
  "unsupported_platform",
]);

function requireApplied(result: NativeVaultTransition | null, operation: string): void {
  if (result !== null && accepted.has(result)) return;
  throw new Error(`Native Vault account fence could not ${operation}. Retry the account action.`);
}

/** One queue for explicit mutations and externally emitted auth events. */
export class NativeVaultHostAuthCoordinator {
  private chain: Promise<void> = Promise.resolve();
  private adoptedSubject: string | null = null;
  private generation = 0;
  private pendingSubject: string | null | undefined = undefined;

  constructor(private readonly commands: NativeVaultCommands) {}

  private serialize<T>(work: () => Promise<T>): Promise<T> {
    const next = this.chain.then(work, work);
    this.chain = next.then(() => undefined, () => undefined);
    return next;
  }

  /** Revoke adoption synchronously, before deferred Supabase callback work. */
  fence(subject?: string | null): number {
    // useAuth and useEngine receive the same Supabase event independently.
    // They share one generation for that event, rather than invalidating each
    // other's deferred work. Undefined is reserved for explicit mutations and
    // always creates a fresh fence.
    if (subject !== undefined && this.pendingSubject === subject) return this.generation;
    this.adoptedSubject = null;
    this.generation += 1;
    this.pendingSubject = subject;
    return this.generation;
  }

  /** Clear dependent permission before an account-changing host auth action. */
  invalidateBeforeHostMutation(): Promise<void> {
    const generation = this.fence();
    return this.serialize(async () => {
      requireApplied(await this.commands.invalidate(), "invalidate");
      // A later event may already have fenced this request. Invalidation still
      // completed, but it must not authorize anything from this older turn.
      if (generation !== this.generation) return;
    });
  }

  /** Reconcile first, then permit dependent adoption in queue order. */
  reconcileAndAdopt<T>(
    subject: string | null,
    adopt?: () => Promise<T> | T,
    existingGeneration?: number,
  ): Promise<T | undefined> {
    const generation = existingGeneration ?? this.fence(subject);
    return this.serialize(async () => {
      requireApplied(await this.commands.reconcile(subject), "reconcile");
      // An auth listener/mutation arrived while this command waited. Its
      // newer account state wins; the old event cannot republish UI/engine use.
      if (generation !== this.generation) return undefined;
      this.adoptedSubject = subject;
      this.pendingSubject = undefined;
      return adopt?.();
    });
  }

  isAdopted(subject: string | null): boolean {
    return subject !== null && this.adoptedSubject === subject;
  }

  adopted(): string | null {
    return this.adoptedSubject;
  }

  adoptedGeneration(subject: string | null): number | null {
    return this.isAdopted(subject) ? this.generation : null;
  }
}

const coordinator = new NativeVaultHostAuthCoordinator({
  invalidate: invalidateNativeVaultHostActor,
  reconcile: reconcileNativeVaultHostActor,
});

export function invalidateNativeVaultBeforeHostMutation(): Promise<void> {
  return coordinator.invalidateBeforeHostMutation();
}

/** Call synchronously inside an auth callback; do no native I/O there. */
export function fenceNativeVaultHostAdoption(subject: string | null): number {
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
  return coordinator.isAdopted(subject ?? null);
}

export function nativeVaultAdoptedHostSubject(): string | null {
  return coordinator.adopted();
}

/** A background task snapshots this and rechecks it immediately before I/O. */
export function nativeVaultAdoptedHostGeneration(subject: string | null | undefined): number | null {
  return coordinator.adoptedGeneration(subject ?? null);
}
