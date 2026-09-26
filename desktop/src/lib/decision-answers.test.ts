/**
 * A decision reply is never an empty bubble in Matrx Local.
 *
 * A decision agent's turn is one `decision_answers` part and no text; the
 * history reader's text fallback found nothing in it and the live stream's
 * data handler only set a status, so the reply showed empty. Use case: All
 * Green Recycling's feedback inbox triaged by the Feedback triage agent from
 * the desktop app's Cloud Chat.
 */
import { describe, expect, it } from "vitest";
import { decisionAnswersText } from "./decision-answers";
import { contentPartToExtracted } from "@/hooks/use-cloud-chat";

function part(defect: boolean, pTrue: number, surface: string) {
  return {
    type: "decision_answers",
    __kind: "decision_answers",
    model: "jev-1.13.0",
    method: "native",
    answers: {
      is_defect: { type: "noul", answer: defect, probability: pTrue, confidence: 0.8 },
      owning_surface: {
        type: "choice",
        answer: surface,
        probabilities: { frontend: 0.15, server: 0.25, [surface]: 0.6 },
        confidence: 0.6,
      },
    },
    unanswerable: { urgency: "The report does not say when pickup was due." },
  };
}

describe.each([
  [false, 0.29, "data", "- is_defect: No (71%)"],
  [true, 0.93, "frontend", "- is_defect: Yes (93%)"],
])("decision reply (defect=%s)", (defect, pTrue, surface, line) => {
  it("reads as its verdict, with the probability of the answer given", () => {
    const text = decisionAnswersText(part(defect, pTrue, surface)) ?? "";
    expect(text).toContain(line);
    expect(text).toContain(`- owning_surface: ${surface} (60%)`);
    expect(text).toContain("- urgency: not answered. The report does not say");
  });

  it("a persisted decision part is not dropped by the history reader", () => {
    const extracted = contentPartToExtracted(part(defect, pTrue, surface));
    expect(extracted.answer).toContain(line);
  });
});

it("a non-decision value is not a guess", () => {
  expect(decisionAnswersText({ text: "hello" })).toBeNull();
});
