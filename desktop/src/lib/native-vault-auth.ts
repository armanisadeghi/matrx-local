/**
 * One serialized bridge from host auth lifecycle into the provider's
 * non-secret generation fence. It intentionally treats host command failure
 * as a fence failure; callers must not adopt dependent state until it settles.
 */
import { invalidateNativeVaultHostActor, reconcileNativeVaultHostActor } from "@/lib/sidecar";

let chain: Promise<void> = Promise.resolve();

function serialize(work: () => Promise<void>): Promise<void> {
  const next = chain.then(work, work);
  chain = next.catch(() => undefined);
  return next;
}

function requireApplied(result: string | null, operation: string) {
  if (result === null || result === "applied" || result === "unchanged" || result === "unsupported_platform") return;
  throw new Error(`Native Vault account fence could not ${operation}. Retry the account action.`);
}

export function invalidateNativeVaultBeforeHostMutation(): Promise<void> {
  return serialize(async () => requireApplied(await invalidateNativeVaultHostActor(), "invalidate"));
}

export function reconcileNativeVaultAfterHostSession(subject: string | null): Promise<void> {
  return serialize(async () => requireApplied(await reconcileNativeVaultHostActor(subject), "reconcile"));
}
