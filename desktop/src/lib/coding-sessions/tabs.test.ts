import { describe, expect, it } from "vitest";

import {
  CODING_SESSIONS_TABS,
  codingSessionsTabHref,
  parseCodingSessionsTab,
} from "@/lib/coding-sessions/tabs";

describe("coding-sessions tab routing", () => {
  it("defaults to the session list", () => {
    expect(parseCodingSessionsTab("")).toBe("sessions");
    expect(parseCodingSessionsTab("?other=1")).toBe("sessions");
  });

  it("reads every real tab out of the URL", () => {
    for (const tab of CODING_SESSIONS_TABS) {
      expect(parseCodingSessionsTab(`?tab=${tab}`)).toBe(tab);
    }
  });

  it("keeps the retired Codex Usage link working as the usage tab", () => {
    // `/codex-usage` redirects here; this is the link it lands on.
    expect(codingSessionsTabHref("usage")).toBe("/coding-sessions?tab=usage");
    expect(parseCodingSessionsTab(new URLSearchParams("tab=usage"))).toBe("usage");
  });

  it("never renders a blank screen for a tab it does not know", () => {
    expect(parseCodingSessionsTab("?tab=nonsense")).toBe("sessions");
    expect(parseCodingSessionsTab("?tab=")).toBe("sessions");
    expect(parseCodingSessionsTab("?tab=USAGE")).toBe("usage");
  });
});
