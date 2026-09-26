/**
 * A `decision_answers` part as readable text.
 *
 * A decision agent's assistant turn is ONE typed `decision_answers` part and
 * no text (contract: common-docs/systems/agents/typed-messages/FEATURE.md).
 * This app has no decision card, and its stream and history readers only knew
 * text, so a decision reply arrived as an EMPTY bubble — a paid, finished
 * verdict dropped without a word. This reads it as one line per answer, with
 * the probability OF THE ANSWER GIVEN (a Yes/No carries P(true), so a `false`
 * at 0.29 is "No (71%)"), then the questions the model refused, with why.
 *
 * Port of matrx-frontend `features/agents/decision-answers/read.ts`
 * (`decisionAnswersText`); the full card with distributions lives there.
 */

export const DECISION_ANSWERS_TYPE = "decision_answers";

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function pct(p: number | null): string {
  return p == null ? "" : ` (${Math.round(p * 100)}%)`;
}

function answerLine(name: string, raw: unknown): string {
  const answer = record(raw);
  if (!answer || answer.answer == null) return `- ${name}: unreadable`;
  const value = answer.answer;
  const distribution = record(answer.probabilities) ?? {};
  if (answer.type === "noul" || typeof value === "boolean") {
    const pTrue = num(answer.probability);
    const p = pTrue == null ? null : value === true ? pTrue : 1 - pTrue;
    return `- ${name}: ${value === true ? "Yes" : "No"}${pct(p)}`;
  }
  if (answer.type === "score") {
    const peak = Object.entries(distribution)
      .filter(([, p]) => num(p) != null)
      .sort((a, b) => (b[1] as number) - (a[1] as number))[0];
    const legend = record(answer.legend) ?? {};
    const label = peak && typeof legend[peak[0]] === "string" ? ` — ${legend[peak[0]]}` : "";
    const shown = typeof value === "number" && !Number.isInteger(value) ? value.toFixed(1) : String(value);
    return `- ${name}: ${shown}${label}${pct(peak ? (peak[1] as number) : null)}`;
  }
  return `- ${name}: ${String(value)}${pct(num(distribution[String(value)]))}`;
}

/** `null` when the value is not a decision payload — never a guess. */
export function decisionAnswersText(payload: unknown): string | null {
  const raw = record(payload);
  const answers = record(raw?.answers);
  if (!raw || !answers) return null;
  const lines = Object.entries(answers).map(([name, answer]) => answerLine(name, answer));
  for (const [name, reason] of Object.entries(record(raw.unanswerable) ?? {})) {
    const why = typeof reason === "string" && reason.trim() ? reason : "No reason was given.";
    lines.push(`- ${name}: not answered. ${why}`);
  }
  if (lines.length === 0) lines.push("- No answers were returned.");
  const method = typeof raw.method === "string" ? raw.method.replace("_", ", ") : null;
  const how = method ? `${method} probabilities` : "probabilities of unknown origin";
  const model = typeof raw.model === "string" ? `, ${raw.model}` : "";
  return [`**Decision** (${how}${model})`, "", ...lines].join("\n");
}

/** True for a part or event payload that carries decision answers. */
export function isDecisionAnswers(value: unknown): boolean {
  const raw = record(value);
  return !!raw && (raw.type === DECISION_ANSWERS_TYPE || raw.__kind === DECISION_ANSWERS_TYPE);
}
