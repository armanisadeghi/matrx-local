import type { MediaRuntimeStatus, RuntimeState } from "@/lib/api";

export const ACTIVE_RUNTIME_STATES = new Set<RuntimeState>([
  "installing",
  "updating",
  "repairing",
  "validating",
  "activating",
]);

export function isRuntimeActive(status: MediaRuntimeStatus | null): boolean {
  return status !== null && ACTIVE_RUNTIME_STATES.has(status.state);
}

export function isRuntimeReady(status: MediaRuntimeStatus | null): boolean {
  return status?.state === "ready" && status.image_available;
}

/**
 * Reject an event from an older operation once an ensure/repair response has
 * given the controller an attempt id. Once an operation is identified, even an
 * untagged event is rejected: accepting it could let a delayed pre-operation
 * snapshot overwrite current progress.
 */
export function acceptsRuntimeSnapshot(
  status: MediaRuntimeStatus,
  expectedAttemptId: string | null,
): boolean {
  return expectedAttemptId === null || status.attempt_id === expectedAttemptId;
}

export function runtimeAction(
  status: MediaRuntimeStatus | null,
): "install" | "update" | "repair" | "restart" | null {
  if (!status) return null;
  switch (status.state) {
    case "absent":
      return "install";
    case "failed":
    case "rolled_back":
      return status.repairable ? "repair" : null;
    case "restart_required":
      return "restart";
    case "ready":
    case "installing":
    case "updating":
    case "repairing":
    case "validating":
    case "activating":
      return null;
  }
}
