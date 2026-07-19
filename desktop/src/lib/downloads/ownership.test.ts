import { describe, expect, it } from "vitest";

import type { DownloadEntry } from "./types";
import { retryBackendFor, usesHuggingFaceHttp } from "./ownership";

function entry(overrides: Partial<DownloadEntry>): DownloadEntry {
  return {
    id: "download-1",
    category: "image_gen",
    filename: "model",
    display_name: "Model",
    urls: [],
    total_bytes: 0,
    bytes_done: 0,
    percent: 0,
    status: "failed",
    error_msg: "blocked",
    priority: 0,
    part_current: 1,
    part_total: 1,
    created_at: "now",
    updated_at: "now",
    completed_at: null,
    ...overrides,
  };
}

describe("download backend ownership", () => {
  it("keeps Python-owned failures on Python even in a packaged Tauri app", () => {
    expect(retryBackendFor(entry({ backend: "python" }))).toBe("python");
    expect(retryBackendFor(entry({ urls: ["hf://org/repo"] }))).toBe("python");
    expect(
      retryBackendFor(entry({ metadata: { civitai_download: true } })),
    ).toBe("python");
  });

  it("keeps explicitly native rows native", () => {
    expect(retryBackendFor(entry({ backend: "rust" }))).toBe("rust");
  });

  it("only sends the HF token for HTTPS Hugging Face requests", () => {
    expect(
      usesHuggingFaceHttp({
        urls: ["https://huggingface.co/org/repo/resolve/main/model.gguf"],
      }),
    ).toBe(true);
    expect(usesHuggingFaceHttp({ urls: ["hf://org/repo"] })).toBe(false);
    expect(usesHuggingFaceHttp({ urls: ["https://example.com/model"] })).toBe(
      false,
    );
  });
});
