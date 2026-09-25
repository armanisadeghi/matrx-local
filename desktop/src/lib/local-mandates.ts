/**
 * Local-model Mandates — the desktop's on-device intelligence, resolved by the
 * PLATFORM, run on the local llama-server.
 *
 * Every intelligence point runs through a Mandate so a Mandate change is
 * guaranteed to take effect (SoR: common-docs/systems/intelligence/mandates/STATE.md
 * §9, §10 Satellites). "Offline" is a data LOCATION, never a different
 * intelligence: the Mandate decides WHICH agent — its instructions, variables,
 * sampling settings and output schema — and this device only supplies the
 * compute. There is no prompt in this repo for these jobs.
 *
 * Flow: GET /api/mandates/{key}/resolution → the Holder's execution definition
 * (GET /api/agents/{id}/execution-definition, or the pinned version's) →
 * substitute variables into ITS messages → run on the local model with ITS
 * settings. Resolves or refuses — there is never a client-side fallback agent
 * or a seed prompt.
 */

import { getAuthedSession } from "@/lib/custodian";
import {
  fetchAgentExecutionDefinition,
  fetchMandateResolution,
  type AgentExecutionDefinition,
  type MandateResolution,
} from "@/lib/aidream-client";
import { requireActiveOrganizationId } from "@/lib/org/active-org";

/**
 * The local-model Mandate keys, declared in aidream
 * `aidream/services/mandates/client_mandates.py` (2026-09-25).
 *
 * TODO(@ai-matrx/agents > 0.13.7): these keys are declared and regenerated into
 * `apps/shared/matrx-agents/mandates/keys.generated.ts` but the package that
 * carries them is not published yet (installed: 0.13.7). When it publishes,
 * replace each literal with `MANDATE_KEYS.local__<job>` from
 * `@ai-matrx/agents/mandates` so a rename or retirement fails type-check here
 * (the way `DEFAULT_CHAT_MANDATE_KEY` in `@/lib/mandates` already does).
 */
export const LOCAL_MODEL_MANDATE_KEYS = {
  polishTranscript: "local.polish_transcript",
  summarizeText: "local.summarize_text",
  improveWriting: "local.improve_writing",
  extractActionItems: "local.extract_action_items",
  explainCode: "local.explain_code",
  answerQuestion: "local.answer_question",
  confidentialChat: "local.confidential_chat",
} as const;

export type LocalModelMandateKey =
  (typeof LOCAL_MODEL_MANDATE_KEYS)[keyof typeof LOCAL_MODEL_MANDATE_KEYS];

/** One authored message of the resolved Holder, flattened to plain text. */
export interface HolderMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

/** Sampling values the Holder declares (absent = the local server's default). */
export interface HolderSettings {
  temperature?: number;
  topP?: number;
  maxTokens?: number;
}

export interface ResolvedLocalMandate {
  mandateKey: LocalModelMandateKey;
  resolution: MandateResolution;
  definition: AgentExecutionDefinition;
  messages: HolderMessage[];
  settings: HolderSettings;
  outputSchema: Record<string, unknown> | null;
}

/**
 * A short cache so a chat turn does not pay two round trips; short enough that
 * a rebind reaches the next run within a minute. The server is the authority.
 */
const CACHE_TTL_MS = 60_000;
const cache = new Map<string, { at: number; value: ResolvedLocalMandate }>();

function textOf(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((block) =>
        block && typeof block === "object" && "text" in block
          ? String((block as { text: unknown }).text ?? "")
          : "",
      )
      .join("");
  }
  return "";
}

function holderMessages(definition: AgentExecutionDefinition): HolderMessage[] {
  const out: HolderMessage[] = [];
  for (const raw of definition.messages) {
    if (!raw || typeof raw !== "object") continue;
    const role = (raw as { role?: unknown }).role;
    if (role !== "system" && role !== "user" && role !== "assistant") continue;
    out.push({ role, content: textOf((raw as { content?: unknown }).content) });
  }
  return out;
}

function num(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function holderSettings(definition: AgentExecutionDefinition): HolderSettings {
  const s = definition.settings ?? {};
  const out: HolderSettings = {};
  const temperature = num(s["temperature"]);
  const topP = num(s["top_p"]);
  const maxTokens = num(s["max_output_tokens"]) ?? num(s["max_tokens"]);
  if (temperature !== undefined) out.temperature = temperature;
  if (topP !== undefined) out.topP = topP;
  if (maxTokens !== undefined) out.maxTokens = maxTokens;
  return out;
}

/**
 * Resolve a local-model Mandate for THIS user and organization and load its
 * Holder's definition. Throws with the Mandate key named when anything fails —
 * the caller refuses the run and shows the message; it never falls back.
 */
export async function resolveLocalMandate(
  mandateKey: LocalModelMandateKey,
  signal?: AbortSignal,
): Promise<ResolvedLocalMandate> {
  const hit = cache.get(mandateKey);
  if (hit && Date.now() - hit.at < CACHE_TTL_MS) return hit.value;
  try {
    const session = await getAuthedSession();
    if (!session?.access_token) {
      throw new Error("Sign in first — this job's instructions come from your AI Matrx account.");
    }
    const organizationId = await requireActiveOrganizationId();
    const resolution = await fetchMandateResolution(
      mandateKey,
      session.access_token,
      organizationId,
      signal,
    );
    const definition = await fetchAgentExecutionDefinition(
      resolution.agent_id,
      resolution.is_version,
      session.access_token,
      organizationId,
      signal,
    );
    const messages = holderMessages(definition);
    if (messages.length === 0) {
      throw new Error(`agent ${resolution.agent_id} has no authored messages`);
    }
    const value: ResolvedLocalMandate = {
      mandateKey,
      resolution,
      definition,
      messages,
      settings: holderSettings(definition),
      outputSchema: definition.output_schema ?? null,
    };
    cache.set(mandateKey, { at: Date.now(), value });
    return value;
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    throw new Error(`Could not resolve ${mandateKey}: ${detail}`);
  }
}

/** Replace `{{name}}` with the value when one is supplied; leave others as authored. */
export function substituteVariables(
  template: string,
  vars: Record<string, string>,
): string {
  return template.replace(/\{\{(\w+)\}\}/g, (match, key: string) =>
    Object.prototype.hasOwnProperty.call(vars, key) ? (vars[key] ?? match) : match,
  );
}

/** Test seam: forget cached resolutions. */
export function clearLocalMandateCache(): void {
  cache.clear();
}
