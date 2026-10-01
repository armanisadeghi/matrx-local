/**
 * THE ONE DOOR for a cloud/local request's `context`.
 *
 * Contract: common-docs/systems/scopes-context/context-delivery/RULES.md.
 *
 * Every context VALUE a desktop turn carries becomes a `ResolvedContextRow`
 * (`@ai-matrx/agents/context`), and the request's `context` is built ONLY by
 * the package's `buildContextWire(rows)` — so an excluded value can never
 * reach the wire and the rows the server's `context_receipt` is checked
 * against are exactly the rows that were sent.
 *
 * Reserved DIRECTIVE keys (`__google_files`) are not values: aidream pops them
 * before its context gate (`context_utils.apply_context_objects`), they never
 * appear in a receipt, and they ship byte-for-byte as they are. They enter
 * through `directives` — typed, never a free-form record.
 *
 * `RequestContextWire` is branded: `buildCloudChatRequest` accepts nothing
 * else, so a future value cannot bypass the door by passing a plain object.
 * Guard: `request-context.test.ts`.
 */

import {
  DEFAULT_INLINE_CAP,
  buildContextWire,
  compareReceipt,
  resolveContextRow,
  withheldKeys,
  type ContextReceipt,
  type ContextReceiptMismatch,
  type ContextRowSource,
  type ResolvedContextRow,
  type SavedContextRuleRows,
} from "@ai-matrx/agents/context";
import type { ContextReceiptData } from "@/types/python-generated/stream-events";
import { GOOGLE_FILES_CONTEXT_KEY } from "@/lib/google-workspace";

declare const requestContextBrand: unique symbol;

/** A request `context` that came out of `buildRequestContext` — nothing else. */
export type RequestContextWire = Readonly<Record<string, unknown>> & {
  readonly [requestContextBrand]: true;
};

/** Reserved directive keys the server pops before its context gate. */
export interface RequestContextDirectives {
  /** Attached Google Docs/Sheets — a plain array of Drive file ids. */
  [GOOGLE_FILES_CONTEXT_KEY]?: readonly string[];
}

export interface RequestContext {
  /** What the turn's values resolved to — the receipt is checked against these. */
  rows: ResolvedContextRow[];
  /** The request body's `context`, or undefined when nothing rides. */
  context: RequestContextWire | undefined;
  /** The request body's `context_withheld` — `withheldKeys` of the same rows. */
  withheld: string[];
}

export function buildRequestContext(input: {
  sources?: readonly ContextRowSource[];
  directives?: RequestContextDirectives;
  savedRules?: SavedContextRuleRows | null;
  cap?: number;
} = {}): RequestContext {
  const { sources = [], directives = {}, savedRules = null, cap = DEFAULT_INLINE_CAP } = input;
  for (const source of sources) {
    if (source.key.startsWith("__")) {
      throw new Error(
        `buildRequestContext: "${source.key}" is a reserved directive key — pass it in directives`,
      );
    }
  }
  const rows = sources.map((source) => resolveContextRow(source, savedRules, cap));
  const wire: Record<string, unknown> = buildContextWire(rows);
  const googleFiles = directives[GOOGLE_FILES_CONTEXT_KEY];
  if (googleFiles && googleFiles.length > 0) {
    wire[GOOGLE_FILES_CONTEXT_KEY] = [...googleFiles];
  }
  return {
    rows,
    context:
      Object.keys(wire).length > 0 ? (wire as unknown as RequestContextWire) : undefined,
    withheld: withheldKeys(rows),
  };
}

/** Normalize the generated wire type (optional fields) to the package's receipt. */
export function toContextReceipt(data: ContextReceiptData): ContextReceipt {
  return {
    version: 1,
    surface: data.surface ?? null,
    cap: data.cap,
    model_reads_context: data.model_reads_context !== false,
    rules_error: data.rules_error ?? null,
    rows: (data.rows ?? []).map((row) => ({
      key: row.key,
      label: row.label,
      surface_key: row.surface_key,
      origin: row.origin,
      chars: row.chars ?? null,
      include: row.include,
      max_inline_chars: row.max_inline_chars,
      delivery: row.delivery,
      decided_by: row.decided_by,
      user_rule: row.user_rule
        ? {
            ...(typeof row.user_rule.include === "boolean"
              ? { include: row.user_rule.include }
              : {}),
            ...(typeof row.user_rule.max_inline_chars === "number"
              ? { max_inline_chars: row.user_rule.max_inline_chars }
              : {}),
          }
        : null,
      clamped: row.clamped ?? false,
      client_sent_excluded: row.client_sent_excluded ?? false,
      // The package models only "model"; a "self_check" strip still surfaces
      // as an include/delivery difference in compareReceipt.
      blocked_by: row.blocked_by === "model" ? "model" : null,
    })),
  };
}

/**
 * Expected vs actual for one turn (RULES.md §6): the rows the request was
 * built from against the receipt the server streamed for it. A value the
 * client sent although the person turned it off, and a rules read the server
 * could not do, are mismatches too.
 */
export function checkContextReceipt(
  expected: readonly ResolvedContextRow[],
  receipt: ContextReceipt,
): ContextReceiptMismatch[] {
  let mismatches = compareReceipt(expected, receipt).mismatches;
  // A model that reads no context received none of it — the truth, not a lie.
  if (!receipt.model_reads_context) {
    mismatches = mismatches.filter((m) => m.field !== "delivery");
  }
  for (const row of receipt.rows) {
    if (row.client_sent_excluded) {
      mismatches.push({ key: row.key, field: "include", expected: false, actual: "sent" });
    }
  }
  if (receipt.rules_error) {
    mismatches.push({ key: "*", field: "user_rule", expected: "read", actual: receipt.rules_error });
  }
  return mismatches;
}

/** One status line for a receipt: "Context: 2 inline · 1 on request · 0 off". */
export function describeContextReceipt(receipt: ContextReceipt): string {
  if (!receipt.model_reads_context) return "Context: this model can't read context.";
  let inline = 0;
  let onRequest = 0;
  let off = 0;
  for (const row of receipt.rows) {
    // Receipt rows carry the server's final delivery (never "server").
    switch (row.delivery) {
      case "inline":
        inline += 1;
        break;
      case "on_request":
        onRequest += 1;
        break;
      case "off":
        off += 1;
        break;
    }
  }
  return `Context: ${inline} inline · ${onRequest} on request · ${off} off.`;
}
