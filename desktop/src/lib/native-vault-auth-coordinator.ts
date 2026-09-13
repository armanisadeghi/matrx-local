import type { NativeVaultTransition } from "@/lib/sidecar";

export interface NativeVaultTransitionContext {
  readonly revision: number;
  readonly nextSubject: string | null;
  readonly isCurrent: () => boolean;
  readonly engineOrigin?: string;
  readonly engineGeneration?: string;
  readonly engineCredentialRevision?: number;
}

export type EngineAlignment =
  | { status: "aligned"; origin: string; generation: string; credentialRevision: number; subject: string | null }
  | { status: "unavailable" | "cleanup_failed" | "superseded" };

export interface NativeVaultCommands {
  invalidate(): Promise<NativeVaultTransition | null>;
  reconcile(subject: string | null): Promise<NativeVaultTransition | null>;
}
export type EngineTransitionParticipant = (context: NativeVaultTransitionContext) => Promise<EngineAlignment>;

const accepted = new Set<NativeVaultTransition>(["applied", "unchanged", "unsupported_platform"]);
function requireApplied(result: NativeVaultTransition | null, operation: string): void {
  if (result !== null && accepted.has(result)) return;
  throw new Error(`Native Vault account fence could not ${operation}. Retry the account action.`);
}

/** Pure, serialized host transition authority. Cleanup uses its own revision context. */
export class NativeVaultHostAuthCoordinator {
  private chain: Promise<void> = Promise.resolve();
  private revision = 0;
  private adoptedSubject: string | null = null;
  private aligned: EngineAlignment | null = null;

  constructor(private readonly commands: NativeVaultCommands, private readonly prepareEngine: EngineTransitionParticipant) {}
  /** A caller may stop waiting, but the queued native operation must retain the lock. */
  private boundedCallerWait<T>(operation: Promise<T>): Promise<T> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    return Promise.race([
      operation,
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error("Account transition timed out. Retry the account action.")), 5_000);
      }),
    ]).finally(() => { if (timer) clearTimeout(timer); });
  }
  private serialize<T>(work: () => Promise<T>): Promise<T> {
    const next = this.chain.then(work, work);
    this.chain = next.then(() => undefined, () => undefined);
    return next;
  }
  fence(_subject?: string | null): number { this.adoptedSubject = null; this.aligned = null; this.revision += 1; return this.revision; }
  isCurrentRevision(revision: number): boolean { return this.revision === revision; }
  currentContext(nextSubject: string | null, revision = this.revision): NativeVaultTransitionContext {
    return { revision, nextSubject, isCurrent: () => this.revision === revision };
  }
  async invalidateBeforeHostMutation(): Promise<void> {
    const context = this.currentContext(null, this.fence());
    const operation = this.serialize(async () => {
      if (!context.isCurrent()) throw new Error("Account transition is no longer current.");
      requireApplied(await this.commands.invalidate(), "invalidate");
      if (!context.isCurrent()) throw new Error("Account transition is no longer current.");
      const alignment = await this.prepareEngine(context);
      if (!context.isCurrent()) throw new Error("Account transition is no longer current.");
      this.aligned = alignment;
      if (alignment.status === "cleanup_failed") throw new Error("Engine account cleanup failed. Retry account cleanup before signing in.");
    });
    return this.boundedCallerWait(operation);
  }
  reconcileAndAdopt<T>(subject: string | null, adopt?: () => Promise<T> | T, revision = this.fence()): Promise<T | undefined> {
    const context = this.currentContext(subject, revision);
    const operation = this.serialize(async () => {
      if (!context.isCurrent()) return undefined;
      requireApplied(await this.commands.reconcile(subject), "reconcile");
      if (!context.isCurrent()) return undefined;
      const alignment = await this.prepareEngine(context);
      if (!context.isCurrent()) return undefined;
      this.aligned = alignment;
      this.adoptedSubject = subject;
      return adopt?.();
    });
    return this.boundedCallerWait(operation);
  }
  isAdopted(subject: string | null | undefined): boolean { return subject !== null && subject !== undefined && this.adoptedSubject === subject; }
  adoptedGeneration(subject: string | null | undefined): number | null { return this.isAdopted(subject) ? this.revision : null; }
  engineContext(subject: string | null | undefined): NativeVaultTransitionContext | null {
    if (!this.isAdopted(subject) || !this.aligned || this.aligned.status !== "aligned") return null;
    if (this.aligned.subject !== null && this.aligned.subject !== subject) return null;
    return {
      ...this.currentContext(subject ?? null),
      engineOrigin: this.aligned.origin,
      engineGeneration: this.aligned.generation,
      engineCredentialRevision: this.aligned.credentialRevision,
    };
  }
  engineAlignment(): EngineAlignment | null { return this.aligned; }
  alignEngineForAdopted(subject: string | null): Promise<EngineAlignment | null> {
    if (!this.isAdopted(subject)) return Promise.resolve(null);
    const context = this.currentContext(subject);
    return this.serialize(async () => {
      if (!context.isCurrent() || !this.isAdopted(subject)) return null;
      const alignment = await this.prepareEngine(context);
      if (!context.isCurrent() || !this.isAdopted(subject)) return null;
      this.aligned = alignment;
      return alignment;
    });
  }
}
