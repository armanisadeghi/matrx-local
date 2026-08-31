/**
 * THE GUARD for the @ai-matrx/agents 0.6.0 adoption (C28 consumer action).
 *
 * Two things this repo handed to the package must never silently change back:
 *
 *  1. WHICH AI surfaces get promoted to `/v2`. The allowlist lives in the
 *     package (`V2_COVERED_AI_PATH_TEMPLATES`), not here — so this file pins
 *     the OUTCOME for every URL this desktop builds, written as string
 *     LITERALS, never derived from the function under test. A change in the
 *     package's allowlist that would move `/resume` onto v2, or drop
 *     `/mandates` off it, fails here.
 *
 *  2. THE ORGANIZATION HEADER has exactly ONE spelling in this repo, and it is
 *     the package's. Two independent spellings of one header is how a rename
 *     turns a fail-closed check into a silent fail-open.
 */

import { applyOrganizationContextHeader } from "@ai-matrx/agents/matrx";
import { describe, expect, it } from "vitest";

import {
  CHAT_PATH,
  agentExecutePath,
  agentTargetExecutePath,
  aiRequestUrl,
  conversationContinuePath,
  conversationResumePath,
  mandateExecutePath,
  mandateResolutionPath,
} from "./routes/ai";

const CLOUD_ROOT = "https://api.example.test/api";
const ENGINE_ROOT = "http://127.0.0.1:22240";
const ORG_ID = "3f2a91c4-5b6d-4e7f-8a90-1b2c3d4e5f60";

describe("AI path helpers write the v1, in-app form", () => {
  it("emits the exact paths the package allowlist is expressed in", () => {
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
    // It hangs off `/api`, not `/api/ai`, so it never reaches `aiRequestUrl`
    // and can never be promoted.
    expect(mandateResolutionPath("local.cloud_chat")).toBe(
      "/mandates/local.cloud_chat/resolution",
    );
  });
});

describe("the cloud target is promoted by the PACKAGE's allowlist", () => {
  it("promotes every run-start surface to /api/v2", () => {
    expect(aiRequestUrl("cloud", CLOUD_ROOT, CHAT_PATH)).toBe(
      "https://api.example.test/api/v2/ai/chat",
    );
    expect(aiRequestUrl("cloud", CLOUD_ROOT, agentExecutePath("agent-1"))).toBe(
      "https://api.example.test/api/v2/ai/agents/agent-1",
    );
    expect(
      aiRequestUrl("cloud", CLOUD_ROOT, mandateExecutePath("local.cloud_chat")),
    ).toBe("https://api.example.test/api/v2/ai/mandates/local.cloud_chat");
    expect(aiRequestUrl("cloud", CLOUD_ROOT, conversationContinuePath("c1"))).toBe(
      "https://api.example.test/api/v2/ai/conversations/c1",
    );
  });

  it("leaves /resume on v1 — there is no /v2 sibling for it", () => {
    expect(aiRequestUrl("cloud", CLOUD_ROOT, conversationResumePath("c1"))).toBe(
      "https://api.example.test/api/ai/conversations/c1/resume",
    );
  });
});

describe("the local engine mirror is NEVER promoted", () => {
  it("calls the engine's v1 routes for every surface", () => {
    expect(aiRequestUrl("local", ENGINE_ROOT, CHAT_PATH)).toBe(
      "http://127.0.0.1:22240/ai/chat",
    );
    expect(aiRequestUrl("local", ENGINE_ROOT, agentExecutePath("agent-1"))).toBe(
      "http://127.0.0.1:22240/ai/agents/agent-1",
    );
    expect(aiRequestUrl("local", ENGINE_ROOT, conversationContinuePath("c1"))).toBe(
      "http://127.0.0.1:22240/ai/conversations/c1",
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
