/**
 * Mandate references for the desktop app (mirror of matrx-extend's
 * `src/lib/agents/mandates.ts`). A Mandate is the platform's named answer to
 * "which agent runs this step" — the agent lives in the DATABASE; this file
 * only names the job. Never an agent UUID here.
 * SoR: common-docs/systems/agents/mandates/FEATURE.md (+ RUNTIME.md).
 */

/** Canonical server-resolved target for a brand-new desktop Cloud Chat. */
export const DEFAULT_CHAT_MANDATE_KEY = "local.cloud_chat" as const;

/**
 * Stable UI identity for a Mandate-backed choice. This is not an agent id and
 * is never sent to an agent-id route; the run path uses the mandate key.
 */
export const DEFAULT_CHAT_MANDATE_REF = `mandate:${DEFAULT_CHAT_MANDATE_KEY}` as const;

export function isMandateAgentRef(value: string | null | undefined): boolean {
  return typeof value === "string" && value.startsWith("mandate:");
}

export function mandateKeyFromAgentRef(value: string | null | undefined): string | null {
  if (!isMandateAgentRef(value)) return null;
  const key = value?.slice("mandate:".length) ?? "";
  return key.length > 0 ? key : null;
}
