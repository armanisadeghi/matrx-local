/**
 * A reloaded Cloud Chat turn never drops a typed part.
 *
 * The history reader fell through to a text fallback for every part kind it
 * did not know; `decision_questions` and `speech_script` carry no text field,
 * so the Feedback triage agent's questions and the ElevenLabs speech agent's
 * script vanished on reload, and any future kind would too. Fixtures are the
 * real persisted shapes (chat.message, admin@admin.com), trimmed of prose.
 */
import { describe, expect, it } from "vitest";
import { contentPartToExtracted } from "@/hooks/use-cloud-chat";
import {
  isMessagePart,
  MESSAGE_FLAG_KEYS,
  type ImageMediaPart,
  type MessageWrapper,
} from "@/types/python-generated/stream-events";

const DECISION_QUESTIONS = {
  type: "decision_questions",
  __kind: "decision_questions",
  metadata: {},
  questions: [
    {
      name: "is_defect",
      type: "noul",
      criteria: { true: "existing behaviour is wrong", false: "new behaviour is wanted" },
      instructions: "Is this a defect in existing behaviour rather than a request for new behaviour?",
      suggested_threshold: 0.7,
    },
  ],
};

const SPEECH_SCRIPT = {
  type: "speech_script",
  __kind: "speech_script",
  metadata: {},
  turns: [
    {
      text: "Good morning and welcome to Deep Dive.",
      voice: "cgSgspJ2msm6clMCkdW9",
      speaker: "Maya",
      direction: "warm and upbeat",
      pause_after_ms: 400,
    },
  ],
};

describe("reloaded typed parts", () => {
  it("decision questions read as the questions that were put", () => {
    const out = contentPartToExtracted(DECISION_QUESTIONS);
    expect(out.answer).toContain("is_defect: Is this a defect");
  });

  it("a speech script reads as its lines, with speakers", () => {
    expect(contentPartToExtracted(SPEECH_SCRIPT).answer).toBe(
      "**Maya:** Good morning and welcome to Deep Dive.",
    );
  });

  it("an unknown future kind is announced, never dropped", () => {
    expect(contentPartToExtracted({ type: "survey_result", score: 7 }).answer).toBe(
      "_[Survey Result — this part cannot be shown here]_",
    );
  });
});

describe("generated persisted-part contract is current", () => {
  it("accepts the typed kinds and a media reference role", () => {
    expect(isMessagePart(DECISION_QUESTIONS)).toBe(true);
    expect(isMessagePart(SPEECH_SCRIPT)).toBe(true);
    const subject: ImageMediaPart = {
      type: "media",
      kind: "image",
      file_id: "11111111-1111-4111-8111-111111111111",
      role: "subject",
      metadata: {},
    } as ImageMediaPart;
    expect(isMessagePart(subject)).toBe(true);
  });

  it("carries message-level flags", () => {
    const message: MessageWrapper = {
      role: "assistant",
      content: [{ type: "text", text: "Dear" } as MessageWrapper["content"][number]],
      flags: { prefill: true },
    };
    expect(MESSAGE_FLAG_KEYS).toContain("prefill");
    expect(message.flags?.prefill).toBe(true);
  });
});
