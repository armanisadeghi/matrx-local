import { describe, expect, it } from "vitest";

import { getDownloadStatusLog } from "./logging";

const failure = {
  id: "llm-model.gguf",
  filename: "model.gguf",
  status: "failed" as const,
  error_msg: "Downloaded file failed validation",
  bytes_done: 0,
  total_bytes: 0,
  updated_at: "2026-07-18T23:20:37Z",
};

describe("download status logging", () => {
  it("reports a restored startup failure as history at warning severity", () => {
    const log = getDownloadStatusLog(failure, "snapshot");

    expect(log?.level).toBe("warn");
    expect(log?.message).toContain("Previous failure restored");
    expect(log?.message).toContain("updated_at=2026-07-18T23:20:37Z");
  });

  it("keeps a failure from a live transfer at error severity", () => {
    const log = getDownloadStatusLog(failure, "live");

    expect(log?.level).toBe("error");
    expect(log?.message).toContain("[downloads] FAILED:");
  });

  it("keeps actionable failures informational for either origin", () => {
    const log = getDownloadStatusLog(
      {
        ...failure,
        resolution: {
          code: "hf_token_missing",
          title: "Add token",
          message: "Add a Hugging Face token.",
          action_kind: "settings_api_keys",
          action_label: "Add token",
          action_url: null,
          provider: "huggingface",
        },
      },
      "snapshot",
    );

    expect(log?.level).toBe("info");
    expect(log?.message).toContain("[action-needed]");
  });

  it("reports a restored cancellation as history instead of a new warning", () => {
    const log = getDownloadStatusLog(
      {
        id: "llm-cancelled.gguf",
        filename: "cancelled.gguf",
        status: "cancelled",
      },
      "snapshot",
    );

    expect(log?.level).toBe("info");
    expect(log?.message).toContain("Previous cancellation restored");
  });

  it("keeps a live cancellation at warning severity", () => {
    const log = getDownloadStatusLog(
      {
        id: "llm-cancelled.gguf",
        filename: "cancelled.gguf",
        status: "cancelled",
      },
      "live",
    );

    expect(log?.level).toBe("warn");
    expect(log?.message).toContain("[downloads] CANCELLED:");
  });
});
