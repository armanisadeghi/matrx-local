import { describe, expect, it } from "vitest";
import {
  customStylePolishOfferedValues,
  transcriptPolishOfferedValues,
} from "./polish-offer";
import type { TranscriptionSession } from "./types";

const session: TranscriptionSession = {
  id: "s-1",
  title: "Standup with the field crew",
  createdAt: "2026-09-27T15:00:00.000Z",
  updatedAt: "2026-09-27T15:12:00.000Z",
  durationSecs: 712,
  charCount: 5400,
  modelUsed: "ggml-base.en.bin",
  deviceUsed: "MacBook Pro Microphone",
  segments: [
    { text: "Morning all.", start_sec: 0, end_sec: 1.2 },
    { text: "Truck two is back.", start_sec: 1.2, end_sec: 3 },
  ],
  fullText: "Morning all. Truck two is back.",
  rawText: "morning all truck two is back",
  aiTitle: "Crew standup",
  aiTags: ["operations", "fleet"],
};

describe("transcriptPolishOfferedValues (local.transcript_polish)", () => {
  it("names every held fact as a string", () => {
    expect(
      transcriptPolishOfferedValues({
        session,
        transcript: "Morning all. Truck two is back.",
        styleName: "Meeting Notes",
      }),
    ).toEqual({
      session_title: "Standup with the field crew",
      duration_secs: "712",
      recorded_at: "2026-09-27T15:00:00.000Z",
      char_count: "31",
      whisper_model: "ggml-base.en.bin",
      audio_device: "MacBook Pro Microphone",
      segment_count: "2",
      previous_ai_tags: "operations, fleet",
      style_name: "Meeting Notes",
    });
  });

  it("omits what an unfinished, untitled session does not know", () => {
    expect(
      transcriptPolishOfferedValues({
        session: {
          ...session,
          title: null,
          durationSecs: 0,
          deviceUsed: null,
          segments: [],
          aiTags: [],
        },
        transcript: "hi",
      }),
    ).toEqual({
      recorded_at: "2026-09-27T15:00:00.000Z",
      char_count: "2",
      whisper_model: "ggml-base.en.bin",
    });
  });
});

describe("customStylePolishOfferedValues (local.transcript_polish_custom_style)", () => {
  it("uses the custom provision's names", () => {
    expect(
      customStylePolishOfferedValues({ session, transcript: "x", styleName: "Pirate" }),
    ).toEqual({
      style_name: "Pirate",
      session_title: "Standup with the field crew",
      recorded_at: "2026-09-27T15:00:00.000Z",
      duration_seconds: "712",
      raw_transcript: "morning all truck two is back",
      previous_ai_title: "Crew standup",
      previous_ai_tags: "operations, fleet",
      whisper_model: "ggml-base.en.bin",
    });
  });
});
