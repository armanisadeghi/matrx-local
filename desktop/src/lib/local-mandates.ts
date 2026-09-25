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
 * Flow: GET {engine}/local-mandates/{key} — the engine resolves the mandate
 * against AIDream as this person in their active organization, keeps that
 * answer (and the Holder's execution definition) in SQLite, and hands back
 * both. With the network down it serves the LAST answer the platform gave,
 * flagged `stale` with the reason (app/services/ai/local_mandates.py). Never a
 * client-side fallback agent or a seed prompt.
 */

import { engine } from "@/lib/api";
import type {
  AgentExecutionDefinition,
  MandateResolution,
} from "@/lib/aidream-client";

/**
 * The local-model Mandate keys, declared in aidream
 * `aidream/services/mandates/client_mandates.py` (2026-09-25, rounds 1 and 2).
 *
 * TODO(@ai-matrx/agents > 0.13.8): round 2's keys are regenerated into
 * `apps/shared/matrx-agents/mandates/keys.generated.ts` but not published yet
 * (installed here: 0.13.7). When the package carries them, replace each literal
 * with `MANDATE_KEYS.local__<job>` from `@ai-matrx/agents/mandates` so a rename
 * or retirement fails type-check here (the way `DEFAULT_CHAT_MANDATE_KEY` in
 * `@/lib/mandates` already does).
 */
export const LOCAL_MODEL_MANDATE_KEYS = {
  polishTranscript: "local.polish_transcript",
  summarizeText: "local.summarize_text",
  improveWriting: "local.improve_writing",
  extractActionItems: "local.extract_action_items",
  explainCode: "local.explain_code",
  answerQuestion: "local.answer_question",
  confidentialChat: "local.confidential_chat",
  /** Confidential Chat's Tools mode (the person's selected local tools). */
  toolCallingChat: "local.tool_calling_chat",
  /** Confidential Chat's Raw JSON mode (the person's hand-written request body). */
  rawCompletion: "local.raw_completion",
  /** Voice page AI Polish — built-in styles and the person's own style. */
  polishStyleStandard: "local.polish_style_standard",
  polishStyleFormal: "local.polish_style_formal",
  polishStyleBullets: "local.polish_style_bullets",
  polishStyleActionItems: "local.polish_style_action_items",
  polishStyleMeetingNotes: "local.polish_style_meeting_notes",
  polishStyleLightCleanup: "local.polish_style_light_cleanup",
  polishStyleCustom: "local.polish_style_custom",
  /** Confidential Chat's built-in prompt library (the Holder's system text). */
  chatPersonaHelpful: "local.chat_persona_helpful",
  chatPersonaTranscriptPolish: "local.chat_persona_transcript_polish",
  chatPersonaSummarize: "local.chat_persona_summarize",
  chatPersonaExplainSimply: "local.chat_persona_explain_simply",
  chatPersonaCodeReview: "local.chat_persona_code_review",
  chatPersonaBrainstorm: "local.chat_persona_brainstorm",
  chatPersonaSpokenReplies: "local.chat_persona_spoken_replies",
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
  /**
   * True when the platform was unreachable and this is the LAST answer it
   * gave this person in this organization (cached on this device).
   */
  stale: boolean;
  /** Why the answer is stale; null when fresh. */
  staleReason: string | null;
}

/**
 * Window event fired when a run used a stale (offline) answer; the app's
 * notification center announces it (hooks/use-notifications.ts).
 */
export const LOCAL_MANDATE_STALE_EVENT = "matrx-local-mandate-stale";

/** What `GET {engine}/local-mandates/{key}` answers. */
interface EngineLocalMandate {
  mandate_key: string;
  resolution: MandateResolution;
  definition: AgentExecutionDefinition;
  resolved_at: string;
  stale: boolean;
  stale_reason: string | null;
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
 * Holder's definition — through the engine, so the last answer survives
 * offline. Throws with the Mandate key named when anything fails — the caller
 * refuses the run and shows the message; it never falls back.
 */
export async function resolveLocalMandate(
  mandateKey: LocalModelMandateKey,
  signal?: AbortSignal,
): Promise<ResolvedLocalMandate> {
  const hit = cache.get(mandateKey);
  if (hit && Date.now() - hit.at < CACHE_TTL_MS) return hit.value;
  try {
    const raw = (await engine.get(
      `/local-mandates/${encodeURIComponent(mandateKey)}`,
      signal ? { signal } : undefined,
    )) as EngineLocalMandate | null;
    if (!raw || typeof raw !== "object" || !raw.resolution || !raw.definition) {
      throw new Error("the engine answered without a resolution and a Holder definition");
    }
    const definition = raw.definition;
    const messages = holderMessages(definition);
    if (messages.length === 0) {
      throw new Error(`agent ${raw.resolution.agent_id} has no authored messages`);
    }
    const value: ResolvedLocalMandate = {
      mandateKey,
      resolution: raw.resolution,
      definition,
      messages,
      settings: holderSettings(definition),
      outputSchema: definition.output_schema ?? null,
      stale: raw.stale === true,
      staleReason: raw.stale === true ? (raw.stale_reason ?? "the platform was unreachable") : null,
    };
    // A stale answer is never cached here: the next run asks again, so a
    // reconnect (or a rebind) reaches it at once.
    if (!value.stale) cache.set(mandateKey, { at: Date.now(), value });
    else if (typeof window !== "undefined") {
      window.dispatchEvent(
        new CustomEvent(LOCAL_MANDATE_STALE_EVENT, {
          detail: { mandateKey, reason: value.staleReason },
        }),
      );
    }
    return value;
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    throw new Error(`Could not resolve ${mandateKey}: ${detail}`);
  }
}

/**
 * The Holder's authored messages that lead a conversation — a system message
 * with no text is dropped (it carries no instructions), everything else is
 * kept in order.
 */
export function holderLeadMessages(holder: ResolvedLocalMandate): HolderMessage[] {
  return holder.messages.filter((m) => m.role !== "system" || m.content.trim() !== "");
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
