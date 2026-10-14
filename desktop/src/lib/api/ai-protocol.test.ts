/**
 * THE GUARD for the AI path helpers and the organization header.
 *
 * Expectations are string LITERALS, never derived from the function under
 * test. The organization header has exactly ONE spelling in this repo, and it
 * is the package's: two independent spellings of one header is how a rename
 * turns a fail-closed check into a silent fail-open.
 */

import { applyOrganizationContextHeader } from "@ai-matrx/api";
import { describe, expect, it } from "vitest";

import {
  CHAT_PATH,
  agentExecutePath,
  agentTargetExecutePath,
  conversationContinuePath,
  conversationResumePath,
  mandateExecutePath,
  mandateResolutionPath,
} from "./routes/ai";

const ORG_ID = "3f2a91c4-5b6d-4e7f-8a90-1b2c3d4e5f60";

describe("AI path helpers write the plain /ai form", () => {
  it("emits the exact paths", () => {
    expect(agentExecutePath("agent-1")).toBe("/ai/agents/agent-1");
    expect(mandateExecutePath("local.cloud_chat")).toBe("/ai/mandates/local.cloud_chat");
    expect(agentTargetExecutePath("agent-1")).toBe("/ai/agents/agent-1");
    expect(agentTargetExecutePath("mandate:local.cloud_chat")).toBe(
      "/ai/mandates/local.cloud_chat",
    );
    expect(conversationContinuePath("c1")).toBe("/ai/conversations/c1");
    expect(conversationResumePath("c1")).toBe("/ai/conversations/c1/resume");
    expect(CHAT_PATH).toBe("/ai/chat");
  });

  it("keeps the Mandate resolution read off the /ai surface entirely", () => {
    // It hangs off `/api`, not `/api/ai`, so it is not an `/ai` surface.
    expect(mandateResolutionPath("local.cloud_chat")).toBe(
      "/mandates/local.cloud_chat/resolution",
    );
  });
});

describe("the organization header has exactly one spelling", () => {
  it("is the package's, and this repo does not type a second one", () => {
    const headers = applyOrganizationContextHeader(
      { Authorization: "Bearer t" },
      ORG_ID,
    );
    expect(headers).toEqual({
      Authorization: "Bearer t",
      "X-Organization-Id": ORG_ID,
    });
    expect(Object.keys(headers).filter((k) => /organization/i.test(k))).toHaveLength(1);
  });

  it("refuses a malformed id rather than binding it", () => {
    expect(() => applyOrganizationContextHeader({}, "org-123")).toThrow(
      /organization ID is invalid/i,
    );
    expect(() => applyOrganizationContextHeader({}, "")).toThrow(
      /Select an organization/i,
    );
  });
});
