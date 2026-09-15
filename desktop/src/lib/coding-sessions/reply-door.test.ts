import { describe, expect, it } from "vitest";

import type { CodingReplyResponderReport } from "@/lib/aidream-client";
import {
  CODING_SESSION_REPLY_SOURCE_FEATURE,
  replyDoorErrorSentence,
  replyDoorView,
} from "@/lib/coding-sessions/reply-door";

function report(
  overrides: Partial<CodingReplyResponderReport> = {},
): CodingReplyResponderReport {
  return {
    conversation_id: "c-1",
    is_coding_session_mirror: true,
    provider: "claude_code",
    origin: "matrx_local",
    composer_label: "Coding Session Responder is answering — Claude Code will not see this reply.",
    can_reply: true,
    ...overrides,
  };
}

describe("the desktop reply door renders the server's verdict, never its own", () => {
  it("names the responder the server named, and lets the reply go", () => {
    const view = replyDoorView({ status: "ready", report: report(), error: null });
    expect(view.isMirror).toBe(true);
    expect(view.label).toBe(
      "Coding Session Responder is answering — Claude Code will not see this reply.",
    );
    expect(view.inputBlocked).toBe(false);
    expect(view.refusalSentence).toBeNull();
    // The reply must file itself as the coding-session reply, or the server
    // never stamps `metadata.origin=ai_matrx_reply` and the agent attribution.
    expect(view.sourceFeature).toBe(CODING_SESSION_REPLY_SOURCE_FEATURE);
    expect(view.sourceFeature).toBe("coding_session_reply");
  });

  it("blocks the composer with the server's own words when can_reply is false", () => {
    const refusal =
      "No AI Matrx agent is available to answer here yet, so a reply cannot be answered.";
    const view = replyDoorView({
      status: "ready",
      report: report({ can_reply: false, composer_label: refusal }),
      error: null,
    });
    expect(view.inputBlocked).toBe(true);
    expect(view.refusalSentence).toBe(refusal);
    expect(view.placeholder).toBe("A reply cannot be sent here.");
  });

  it("falls back to the server's reason when it refused without a label", () => {
    const view = replyDoorView({
      status: "ready",
      report: report({
        can_reply: false,
        composer_label: "",
        is_coding_session_mirror: false,
        reason: "This conversation does not belong to this account.",
      }),
      error: null,
    });
    expect(view.label).toBeNull();
    expect(view.refusalSentence).toBe("This conversation does not belong to this account.");
    expect(view.inputBlocked).toBe(true);
  });

  it("surfaces the stand-in notice only when a platform default answered", () => {
    const withStandIn = replyDoorView({
      status: "ready",
      report: report({
        responder: {
          agent_id: "a-1",
          agent_name: "Default Chat",
          setting_key: "coding_session.conversation_responder",
          used_platform_default: true,
        },
        stand_in_notice: "No agent is chosen for coding-conversation replies yet.",
      }),
      error: null,
    });
    expect(withStandIn.standInNotice).toBe(
      "No agent is chosen for coding-conversation replies yet.",
    );

    const bound = replyDoorView({
      status: "ready",
      report: report({
        responder: {
          agent_id: "a-1",
          agent_name: "Coding Session Responder",
          setting_key: "coding_session.conversation_responder",
          used_platform_default: false,
        },
        stand_in_notice: "should not be shown",
      }),
      error: null,
    });
    expect(bound.standInNotice).toBeNull();
  });

  it("never offers a send before the server has answered", () => {
    const view = replyDoorView({ status: "loading", report: null, error: null });
    expect(view.inputBlocked).toBe(true);
    expect(view.label).toBeNull();
  });

  it("does not turn a failed read into a refusal, and says what failed", () => {
    const view = replyDoorView({
      status: "error",
      report: null,
      error: "HTTP 503 Service Unavailable",
    });
    expect(view.inputBlocked).toBe(false);
    expect(view.sourceFeature).toBeNull();
    expect(replyDoorErrorSentence("HTTP 503 Service Unavailable")).toBe(
      "Who answers here could not be loaded, so this screen is not naming anyone: " +
        "HTTP 503 Service Unavailable",
    );
  });

  it("leaves an ordinary chat conversation completely alone", () => {
    const view = replyDoorView({
      status: "ready",
      report: report({
        is_coding_session_mirror: false,
        composer_label: "",
        provider: null,
        origin: null,
      }),
      error: null,
    });
    expect(view.isMirror).toBe(false);
    expect(view.label).toBeNull();
    expect(view.inputBlocked).toBe(false);
    expect(view.sourceFeature).toBeNull();
  });
});
