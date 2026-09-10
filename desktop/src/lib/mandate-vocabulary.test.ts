/**
 * `@ai-matrx/agents` 0.10.0's Consumer action: the desktop stops NAMING
 * mandates. Its key comes from the package's generated vocabulary, so a rename
 * or a retirement on the server fails type-check here instead of 404ing in the
 * app.
 *
 * Proven RED: re-typing the key as a literal the platform does not declare
 * turns the first two assertions red; renaming the `MANDATE_KEYS` member turns
 * `tsc` red.
 */

import { describe, expect, it } from "vitest";
import { MANDATE_KEYS, isMandateKey } from "@ai-matrx/agents/mandates";
import {
  DEFAULT_CHAT_MANDATE_KEY,
  DEFAULT_CHAT_MANDATE_REF,
  isMandateAgentRef,
  mandateKeyFromAgentRef,
} from "@/lib/mandates";

describe("mandate vocabulary", () => {
  it("the key this app ships is one the platform declares", () => {
    expect(isMandateKey(DEFAULT_CHAT_MANDATE_KEY)).toBe(true);
  });

  it("sources it from the package, not from a local literal", () => {
    expect(DEFAULT_CHAT_MANDATE_KEY).toBe(MANDATE_KEYS.local__cloud_chat);
  });

  it("the ref carries the key and resolves back to it", () => {
    expect(DEFAULT_CHAT_MANDATE_REF).toBe(
      `mandate:${DEFAULT_CHAT_MANDATE_KEY}`,
    );
    expect(mandateKeyFromAgentRef(DEFAULT_CHAT_MANDATE_REF)).toBe(
      DEFAULT_CHAT_MANDATE_KEY,
    );
  });

  it("routes a ref the SERVER may know and this build may not — routing is not a vocabulary check", () => {
    expect(isMandateAgentRef("mandate:declared.after.this.build")).toBe(true);
    expect(mandateKeyFromAgentRef("mandate:declared.after.this.build")).toBe(
      "declared.after.this.build",
    );
    expect(mandateKeyFromAgentRef("mandate:")).toBeNull();
    expect(mandateKeyFromAgentRef("some-agent-uuid")).toBeNull();
    expect(mandateKeyFromAgentRef(null)).toBeNull();
  });
});
