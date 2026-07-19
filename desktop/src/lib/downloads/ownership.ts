import type { DownloadEntry } from "./types";

export type DownloadBackend = "rust" | "python";

/** Determine which manager must own a retry. Transport tags are authoritative. */
export function retryBackendFor(entry: DownloadEntry): DownloadBackend {
  if (entry.backend) return entry.backend;
  if (
    entry.urls?.some((url) => url.startsWith("hf://")) ||
    entry.metadata?.hf_repo_id != null ||
    entry.metadata?.civitai_download === true
  ) {
    return "python";
  }
  return entry.category === "llm" || entry.category === "whisper"
    ? "rust"
    : "python";
}

export function usesHuggingFaceHttp(entry: { urls: string[] }): boolean {
  return entry.urls.some((url) => {
    try {
      const parsed = new URL(url);
      return (
        parsed.protocol === "https:" &&
        (parsed.hostname === "huggingface.co" ||
          parsed.hostname.endsWith(".huggingface.co"))
      );
    } catch {
      return false;
    }
  });
}
