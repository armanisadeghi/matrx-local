/**
 * useLlmPipeline
 *
 * Runs a local-model MANDATE on the on-device llama-server. The platform
 * decides which agent holds the job — its instructions, user template,
 * variables, sampling settings and output schema — and this hook only supplies
 * the compute (common-docs/systems/intelligence/mandates/STATE.md §9–§10).
 * There is no prompt in this file: the templates that used to live here were
 * mandate bypasses and are now the system Holders of the `local.*` mandates.
 *
 * Usage:
 *   const { run, running, error } = useLlmPipeline(() => port);
 *   const result = await run(LOCAL_MODEL_MANDATE_KEYS.polishTranscript, {
 *     transcript: rawText,
 *   });
 *
 * A resolution failure refuses the run with the mandate named — never a
 * fallback prompt. If the local server is not running, run() throws.
 *
 * New job? Declare a mandate in aidream (`client_mandates.py`), seed its
 * Holder, and call run() with its key. Never add a prompt here.
 */

import { useState, useCallback } from "react";
import { chatCompletion, structuredOutput } from "@/lib/llm/api";
import {
  resolveLocalMandate,
  substituteVariables,
  type LocalModelMandateKey,
} from "@/lib/local-mandates";

// ── Output shapes ─────────────────────────────────────────────────────────

export interface TranscriptPolishOutput {
  title: string;
  cleaned: string;
  description: string;
  tags: string[];
}

/**
 * Robustly parse the LLM response for a polish_transcript run.
 *
 * Small models often produce malformed JSON (missing fields, wrong types,
 * markdown code fences, trailing commas, etc.). This parser:
 *   1. Strips markdown fences (```json … ```) if present.
 *   2. Attempts JSON.parse.
 *   3. Extracts each field individually with safe fallbacks — a missing or
 *      wrong-type field never throws; it just falls back to a sensible default.
 *   4. Normalises tags to string[] regardless of what the model returned
 *      (comma-separated string, array of non-strings, undefined, etc.).
 *
 * Returns a fully-typed TranscriptPolishOutput — never throws.
 */
export function parsePolishOutput(
  raw: unknown,
  fallbackTitle: string,
  fallbackText: string,
): TranscriptPolishOutput {
  // Accept pre-parsed objects (structuredOutput path) or raw strings
  let obj: Record<string, unknown> = {};

  if (typeof raw === "object" && raw !== null && !Array.isArray(raw)) {
    obj = raw as Record<string, unknown>;
  } else if (typeof raw === "string") {
    // Strip markdown code fences: ```json … ``` or ``` … ```
    let s = raw.trim();
    const fenceMatch = s.match(/^```(?:json)?\s*([\s\S]*?)```\s*$/i);
    if (fenceMatch) s = (fenceMatch[1] ?? "").trim();

    // Find the outermost JSON object even if there's surrounding prose
    const objMatch = s.match(/\{[\s\S]*\}/);
    if (objMatch) {
      try {
        obj = JSON.parse(objMatch[0]) as Record<string, unknown>;
      } catch {
        // If parse fails, try to extract individual fields via regex as last resort
        const titleMatch = s.match(/"title"\s*:\s*"([^"]+)"/);
        const cleanedMatch = s.match(
          /"cleaned"\s*:\s*"([\s\S]*?)(?=",\s*"|"\s*})/,
        );
        const descMatch = s.match(/"description"\s*:\s*"([^"]+)"/);
        return {
          title: titleMatch?.[1]?.trim() || fallbackTitle,
          cleaned: cleanedMatch?.[1]?.trim() || fallbackText,
          description: descMatch?.[1]?.trim() || "",
          tags: [],
        };
      }
    }
  }

  // Extract each field with safe type coercion
  const title =
    typeof obj.title === "string" && obj.title.trim()
      ? obj.title.trim()
      : fallbackTitle;

  const cleaned =
    typeof obj.cleaned === "string" && obj.cleaned.trim()
      ? obj.cleaned.trim()
      : fallbackText;

  const description =
    typeof obj.description === "string" ? obj.description.trim() : "";

  // Normalise tags: handle string, string[], mixed arrays, comma-separated strings
  let tags: string[] = [];
  if (Array.isArray(obj.tags)) {
    tags = obj.tags
      .filter((t) => t != null)
      .map((t) => String(t).trim())
      .filter((t) => t.length > 0)
      .slice(0, 8);
  } else if (typeof obj.tags === "string" && obj.tags.trim()) {
    tags = obj.tags
      .split(/[,;]+/)
      .map((t) => t.trim())
      .filter((t) => t.length > 0)
      .slice(0, 8);
  }

  return { title, cleaned, description, tags };
}

// ── Hook types ────────────────────────────────────────────────────────────

export interface PipelineRunOptions {
  /** Signal to abort the request. */
  signal?: AbortSignal;
}

export interface UseLlmPipelineReturn {
  /**
   * Resolve a local-model mandate and run its Holder on the local model.
   * @param mandateKey - One of LOCAL_MODEL_MANDATE_KEYS.
   * @param vars - Values for the Holder's {{variables}}.
   * @param options - Run-scope overrides.
   * @returns The model's response as a string (or parsed object when the Holder declares an output schema).
   */
  run: <T = string>(
    mandateKey: LocalModelMandateKey,
    vars?: Record<string, string>,
    options?: PipelineRunOptions,
  ) => Promise<T>;

  /** True while a run() is in progress. */
  running: boolean;

  /** Error message from the last failed run(). Cleared at the start of each run. */
  error: string | null;

  /** Clears the error state. */
  clearError: () => void;
}

// ── The run (pure; the hook below only adds React state) ─────────────────

/**
 * Resolve `mandateKey`, fill the Holder's authored messages with `vars`, and
 * run them on the local llama-server at `port` with the Holder's settings —
 * structured output when the Holder declares an output schema. Throws (naming
 * the mandate) when the platform cannot resolve it; never falls back.
 */
export async function runLocalMandate<T = string>(
  port: number,
  mandateKey: LocalModelMandateKey,
  vars: Record<string, string> = {},
  options: PipelineRunOptions = {},
): Promise<T> {
  // The Holder's messages, filled with the run's variables — nothing replaces
  // them: a person's own text reaches a Holder only as a declared variable
  // (e.g. local.polish_style_custom's {{style_instructions}}).
  const holder = await resolveLocalMandate(mandateKey, options.signal);
  const messages = holder.messages.map((m) => ({
    role: m.role,
    content: substituteVariables(m.content, vars),
  }));

  const maxTokens = holder.settings.maxTokens;
  const temperature = holder.settings.temperature;
  const sampling = {
    ...(maxTokens !== undefined ? { maxTokens } : {}),
    ...(temperature !== undefined ? { temperature } : {}),
  };

  if (holder.outputSchema) {
    return structuredOutput<T>(port, messages, holder.outputSchema, sampling);
  }
  return (await chatCompletion(port, messages, sampling)) as T;
}

// ── The hook ──────────────────────────────────────────────────────────────

/**
 * @param getPort - A function that returns the current llama-server port, or null if not running.
 *   Typically: () => serverStatus?.port ?? null
 *   Pass it as a function (not value) so the hook always reads the latest state.
 */
export function useLlmPipeline(
  getPort: () => number | null,
): UseLlmPipelineReturn {
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const clearError = useCallback(() => setError(null), []);

  const run = useCallback(
    async <T = string>(
      mandateKey: LocalModelMandateKey,
      vars: Record<string, string> = {},
      options: PipelineRunOptions = {},
    ): Promise<T> => {
      const port = getPort();
      if (!port) {
        throw new Error(
          "Confidential chat is not running. Open Confidential Chat and start your model from Setup.",
        );
      }

      setRunning(true);
      setError(null);

      try {
        return await runLocalMandate<T>(port, mandateKey, vars, options);
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setError(msg);
        throw e;
      } finally {
        setRunning(false);
      }
    },
    [getPort],
  );

  return { run, running, error, clearError };
}
