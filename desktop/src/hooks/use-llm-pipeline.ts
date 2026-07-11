/**
 * useLlmPipeline
 *
 * A reusable hook for running named LLM tasks against the local llama-server.
 * Each task is defined as a template — a system prompt + a user prompt template
 * with {{variable}} placeholders. Variables are substituted at call time.
 *
 * Usage:
 *   const { run, running, error } = useLlmPipeline();
 *
 *   // Run a built-in task:
 *   const result = await run("polish_transcript", { transcript: rawText });
 *
 *   // Run with a custom template ad-hoc:
 *   const result = await run({ system: "...", user: "{{text}}" }, { text: "..." });
 *
 * The hook reads the LLM server port from the llama-server status.
 * If the server is not running, run() throws with a clear message.
 *
 * Adding new templates: add an entry to PIPELINE_TEMPLATES below.
 */

import { useState, useCallback } from "react";
import { chatCompletion, structuredOutput } from "@/lib/llm/api";

// ── Template definitions ──────────────────────────────────────────────────

export interface PipelineTemplate {
  /** Short description shown in the UI. */
  description: string;
  /** System prompt — plain text, no variables. */
  system: string;
  /** User prompt — may contain {{variable}} placeholders. */
  user: string;
  /**
   * If set, the model output is parsed as JSON matching this schema.
   * structuredOutput() is used instead of chatCompletion().
   */
  outputSchema?: object;
  /** Max tokens for the completion. Defaults to 2048. */
  maxTokens?: number;
  /** Temperature override. Defaults to 0.3 for deterministic tasks. */
  temperature?: number;
}

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

/**
 * Named templates available via run(templateName, vars).
 * Add new entries here to extend the pipeline.
 */
export const PIPELINE_TEMPLATES: Record<string, PipelineTemplate> = {
  // ── Voice / transcription ────────────────────────────────────────────────
  polish_transcript: {
    description:
      "Clean up a voice transcript, generate a title, description and tags",
    system:
      "You are an expert editor specializing in spoken-word transcripts. " +
      "Your job is to produce clean, well-punctuated prose from raw speech. " +
      "Rules: fix punctuation and capitalization; remove filler words (um, uh, like, you know, sort of); " +
      "merge run-on sentences into clear, complete sentences; preserve the speaker's exact meaning and vocabulary; " +
      "do not add any content that was not spoken. " +
      "Also generate: " +
      "(1) a short descriptive title of 5–8 words capturing the main topic; " +
      "(2) a one-sentence description summarising what was said; " +
      "(3) an array of 2–5 short topic tags (single words or short phrases, lowercase). " +
      'Return ONLY a JSON object with exactly four fields: "title" (string), "description" (string), ' +
      '"tags" (array of strings), and "cleaned" (string). No markdown, no extra text.',
    user: "Transcript:\n\n{{transcript}}",
    outputSchema: {
      type: "object",
      properties: {
        title: { type: "string" },
        description: { type: "string" },
        tags: { type: "array", items: { type: "string" } },
        cleaned: { type: "string" },
      },
      required: ["title", "description", "tags", "cleaned"],
      additionalProperties: false,
    },
    maxTokens: 4096,
    temperature: 0.2,
  },

  // ── Writing aids ─────────────────────────────────────────────────────────
  summarize: {
    description: "Summarize text concisely",
    system:
      "Summarize the provided text. Capture all key points in clear, structured prose. " +
      "Use bullet points only if the source is a list. Return only the summary with no preamble.",
    user: "Text to summarize:\n\n{{text}}",
    maxTokens: 1024,
    temperature: 0.3,
  },

  improve_writing: {
    description: "Improve clarity and style",
    system:
      "Rewrite the provided text to improve clarity, grammar, and flow. " +
      "Preserve the author's meaning and voice. Fix grammatical errors and awkward phrasing. " +
      "Return only the improved text with no explanation.",
    user: "{{text}}",
    maxTokens: 4096,
    temperature: 0.4,
  },

  extract_action_items: {
    description: "Extract action items from text",
    system:
      "Extract all action items, tasks, and commitments from the provided text. " +
      "Format as a numbered list. Each item should be actionable and specific. " +
      "If no action items are found, return 'No action items found.'",
    user: "{{text}}",
    maxTokens: 1024,
    temperature: 0.1,
  },

  // ── Development ──────────────────────────────────────────────────────────
  explain_code: {
    description: "Explain what code does",
    system:
      "Explain what the following code does in plain English. " +
      "Start with a one-sentence summary, then describe the key steps. " +
      "Assume the reader is a developer but may not know this specific library or language.",
    user: "```\n{{code}}\n```",
    maxTokens: 1024,
    temperature: 0.3,
  },

  // ── Generic ──────────────────────────────────────────────────────────────
  answer_question: {
    description: "Answer a question directly",
    system:
      "Answer the question directly and concisely. " +
      "If you are uncertain, say so. Do not add unnecessary preamble.",
    user: "{{question}}",
    maxTokens: 2048,
    temperature: 0.5,
  },
};

// ── Variable substitution ─────────────────────────────────────────────────

function substituteVars(
  template: string,
  vars: Record<string, string>,
): string {
  return template.replace(/\{\{(\w+)\}\}/g, (match, key) => {
    return Object.prototype.hasOwnProperty.call(vars, key) ? vars[key] ?? match : match;
  });
}

// ── Hook types ────────────────────────────────────────────────────────────

export type TemplateNameOrInline =
  | keyof typeof PIPELINE_TEMPLATES
  | PipelineTemplate;

export interface PipelineRunOptions {
  /** Override max tokens for this run. */
  maxTokens?: number;
  /** Override temperature for this run. */
  temperature?: number;
  /** Signal to abort the request. */
  signal?: AbortSignal;
}

export interface UseLlmPipelineReturn {
  /**
   * Run a named template or an inline template definition.
   * @param template - Template name from PIPELINE_TEMPLATES, or an inline PipelineTemplate object.
   * @param vars - Variable values to substitute into {{placeholders}}.
   * @param options - Optional overrides.
   * @returns The model's response as a string (or parsed object if outputSchema is set).
   */
  run: <T = string>(
    template: TemplateNameOrInline,
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
      template: TemplateNameOrInline,
      vars: Record<string, string> = {},
      options: PipelineRunOptions = {},
    ): Promise<T> => {
      const port = getPort();
      if (!port) {
        throw new Error(
          "Confidential chat is not running. Open Confidential Chat and start your model from Setup.",
        );
      }

      // Resolve template
      const tpl: PipelineTemplate | undefined =
        typeof template === "string" ? PIPELINE_TEMPLATES[template] : template;

      if (!tpl) {
        throw new Error(`Unknown pipeline template: "${String(template)}"`);
      }

      const userContent = substituteVars(tpl.user, vars);
      const messages = [
        { role: "system" as const, content: tpl.system },
        { role: "user" as const, content: userContent },
      ];

      const maxTokens = options.maxTokens ?? tpl.maxTokens ?? 2048;
      const temperature = options.temperature ?? tpl.temperature ?? 0.3;

      setRunning(true);
      setError(null);

      try {
        if (tpl.outputSchema) {
          const result = await structuredOutput<T>(
            port,
            messages,
            tpl.outputSchema,
          );
          return result;
        } else {
          const text = await chatCompletion(port, messages, {
            maxTokens,
            temperature,
          });
          return text as T;
        }
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
