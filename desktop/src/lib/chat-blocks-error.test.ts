import { describe, expect, it } from "vitest";
import type { ErrorPayload } from "@/types/python-generated/stream-events";
import { StreamBlockBuilder } from "./chat-blocks";

describe("StreamBlockBuilder.addError", () => {
  it("shows the server's user_message, never the provider's raw text", () => {
    const billing =
      "OpenAI refused this request: the platform's OpenAI account is out of credit.";
    const builder = new StreamBlockBuilder();
    builder.addError({
      error_type: "insufficient_quota",
      message: "provider said 429 insufficient_quota",
      user_message: billing,
    } as ErrorPayload);
    expect(builder.snapshot()).toEqual([
      { type: "error", errorType: "insufficient_quota", message: billing },
    ]);
  });

  it("falls back to message when user_message is blank", () => {
    const builder = new StreamBlockBuilder();
    builder.addError({
      error_type: "internal",
      message: "The stream failed.",
      user_message: "   ",
    } as ErrorPayload);
    expect(builder.snapshot()[0]).toMatchObject({ message: "The stream failed." });
  });
});
