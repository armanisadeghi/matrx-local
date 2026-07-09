import { engine } from "@/lib/api";
import supabase from "@/lib/supabase";
import {
  STREAM_TAG_CHUNK,
  STREAM_TAG_END,
  STREAM_TAG_ERROR,
  type TtsStatus,
  type TtsVoice,
  type SynthesizeRequest,
  type DownloadResponse,
  type TtsStreamErrorPayload,
  type TtsModelNotDownloadedPayload,
} from "./types";

/** Typed error thrown by synthesizeStream when the server emits an error frame
 *  or the stream is truncated before an end frame arrives. */
export class TtsStreamError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.name = "TtsStreamError";
    this.code = code;
  }
}

/**
 * Thrown when a synth/stream call returns HTTP 409 because the TTS model isn't
 * downloaded yet. Extends TtsStreamError (code `model_not_downloaded`) so the
 * existing `instanceof TtsStreamError` handling in the hooks still routes it,
 * while carrying the `downloading` flag so callers can distinguish
 * "download in progress" from "never downloaded".
 */
export class TtsModelNotDownloadedError extends TtsStreamError {
  needsDownload = true;
  downloading: boolean;
  constructor(message: string, downloading: boolean) {
    super("model_not_downloaded", message);
    this.name = "TtsModelNotDownloadedError";
    this.downloading = downloading;
  }
}

/**
 * If `resp` is the backend's 409 "model not downloaded" contract, throw a
 * typed TtsModelNotDownloadedError. Uses a cloned body so the caller can still
 * read `resp.text()` for any other non-ok status. No-op otherwise.
 */
async function throwIfModelMissing(resp: Response): Promise<void> {
  if (resp.status !== 409) return;
  let payload: Partial<TtsModelNotDownloadedPayload> = {};
  try {
    payload = (await resp.clone().json()) as Partial<TtsModelNotDownloadedPayload>;
  } catch {
    // Not the JSON contract we expect — fall through to generic handling.
  }
  if (payload?.needs_download) {
    throw new TtsModelNotDownloadedError(
      payload.detail ?? "tts model not downloaded",
      Boolean(payload.downloading),
    );
  }
}

/** Sanity ceiling on a single WAV chunk (50 MB). Real chunks are <500 KB. */
const MAX_FRAME_BYTES = 50 * 1024 * 1024;

/** Per-frame read timeout (ms). If no bytes arrive for this long the stream
 *  is considered dead and we abort. Synthesis chunks usually arrive < 2s. */
const FRAME_READ_TIMEOUT_MS = 30_000;

function ttsUrl(base: string, path: string): string {
  return `${base}/tts${path}`;
}

async function authHeaders(): Promise<Record<string, string>> {
  try {
    const {
      data: { session },
    } = await supabase.auth.getSession();
    if (session?.access_token) {
      return { Authorization: `Bearer ${session.access_token}` };
    }
  } catch {
    // auth not available
  }
  return {};
}

async function ttsJson<T>(url: string, init?: RequestInit): Promise<T> {
  const auth = await authHeaders();
  const resp = await fetch(url, {
    ...init,
    headers: {
      ...auth,
      ...(init?.headers as Record<string, string> | undefined),
    },
  });
  if (!resp.ok) {
    const detail = await resp.text().catch(() => resp.statusText);
    throw new Error(`TTS request failed (${resp.status}): ${detail}`);
  }
  return resp.json();
}

export async function getTtsStatus(): Promise<TtsStatus> {
  const base = engine.engineUrl;
  if (!base) throw new Error("Engine not discovered");
  return ttsJson<TtsStatus>(ttsUrl(base, "/status"));
}

export async function getTtsVoices(): Promise<TtsVoice[]> {
  const base = engine.engineUrl;
  if (!base) throw new Error("Engine not discovered");
  return ttsJson<TtsVoice[]>(ttsUrl(base, "/voices"));
}

export async function downloadTtsModel(): Promise<DownloadResponse> {
  const base = engine.engineUrl;
  if (!base) throw new Error("Engine not discovered");
  return ttsJson<DownloadResponse>(ttsUrl(base, "/download-model"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
}

/**
 * Synthesize speech and return the audio as a Blob.
 * The response is raw audio/wav bytes, not JSON.
 */
export async function synthesize(
  req: SynthesizeRequest,
): Promise<{ blob: Blob; duration: number; elapsed: number; voiceId: string }> {
  const base = engine.engineUrl;
  if (!base) throw new Error("Engine not discovered");

  const auth = await authHeaders();
  const resp = await fetch(ttsUrl(base, "/synthesize"), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...auth },
    body: JSON.stringify(req),
  });

  if (!resp.ok) {
    await throwIfModelMissing(resp);
    const detail = await resp.text().catch(() => resp.statusText);
    throw new Error(`TTS synthesis failed (${resp.status}): ${detail}`);
  }

  const blob = await resp.blob();
  const duration = parseFloat(resp.headers.get("X-TTS-Duration") ?? "0");
  const elapsed = parseFloat(resp.headers.get("X-TTS-Elapsed") ?? "0");
  const voiceId = resp.headers.get("X-TTS-Voice") ?? "";

  return { blob, duration, elapsed, voiceId };
}

/**
 * Preview a voice — returns a short audio clip as a Blob.
 */
export async function previewVoice(
  voiceId: string,
): Promise<{ blob: Blob; duration: number }> {
  const base = engine.engineUrl;
  if (!base) throw new Error("Engine not discovered");

  const auth = await authHeaders();
  const resp = await fetch(ttsUrl(base, "/preview-voice"), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...auth },
    body: JSON.stringify({ voice_id: voiceId }),
  });

  if (!resp.ok) {
    await throwIfModelMissing(resp);
    const detail = await resp.text().catch(() => resp.statusText);
    throw new Error(`TTS preview failed (${resp.status}): ${detail}`);
  }

  const blob = await resp.blob();
  const duration = parseFloat(resp.headers.get("X-TTS-Duration") ?? "0");
  return { blob, duration };
}

/**
 * Stream synthesis — async iterator of WAV Blobs.
 *
 * Wire protocol v2 (matches ``app/services/tts/models.py``):
 *   Each frame: 1 byte tag · 4 bytes BE uint32 length · N bytes payload
 *   Tags:
 *     0x01 CHUNK — payload is a complete WAV blob (yielded as Blob)
 *     0x02 END   — clean end-of-stream (returns from the generator)
 *     0xFF ERROR — payload is UTF-8 JSON {code, message} (throws TtsStreamError)
 *
 * The generator only resolves successfully when an END frame is received.
 * If the response body ends before END the generator throws
 * ``TtsStreamError("truncated", ...)`` so the caller can surface a real error
 * instead of treating partial audio as success.
 *
 * Per-frame read timeout (30s) + 50 MB frame ceiling guard against hung
 * connections and runaway allocations.
 */
export async function* synthesizeStream(
  req: SynthesizeRequest,
  signal?: AbortSignal,
): AsyncGenerator<Blob> {
  const base = engine.engineUrl;
  if (!base) throw new Error("Engine not discovered");

  const auth = await authHeaders();
  const resp = await fetch(ttsUrl(base, "/synthesize-stream"), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...auth },
    body: JSON.stringify(req),
    signal,
  });

  if (!resp.ok) {
    await throwIfModelMissing(resp);
    const detail = await resp.text().catch(() => resp.statusText);
    throw new TtsStreamError(
      "http_" + resp.status,
      `TTS stream failed (${resp.status}): ${detail}`,
    );
  }
  if (!resp.body) {
    throw new TtsStreamError("no_body", "No response body for TTS stream");
  }

  const reader = resp.body.getReader();

  // Rolling byte buffer with O(1) consume by maintaining a head pointer.
  let chunks: Uint8Array[] = [];
  let chunksLen = 0;

  function append(chunk: Uint8Array) {
    chunks.push(chunk);
    chunksLen += chunk.byteLength;
  }

  function consume(n: number): Uint8Array {
    if (n > chunksLen) throw new Error("internal: consume past buffer");
    const out = new Uint8Array(n);
    let pos = 0;
    while (pos < n) {
      const head = chunks[0];
      const need = n - pos;
      if (head.byteLength <= need) {
        out.set(head, pos);
        pos += head.byteLength;
        chunks.shift();
      } else {
        out.set(head.subarray(0, need), pos);
        chunks[0] = head.subarray(need);
        pos = n;
      }
    }
    chunksLen -= n;
    return out;
  }

  async function readMore(): Promise<boolean> {
    // True on bytes received, false on clean EOF.
    let timer: ReturnType<typeof setTimeout> | undefined;
    const timeoutPromise = new Promise<{ timedOut: true }>((resolve) => {
      timer = setTimeout(() => resolve({ timedOut: true }), FRAME_READ_TIMEOUT_MS);
    });
    try {
      const result = await Promise.race([
        reader.read().then((r) => ({ ...r, timedOut: false as const })),
        timeoutPromise,
      ]);
      if ("timedOut" in result && result.timedOut) {
        throw new TtsStreamError(
          "frame_timeout",
          `No data received from TTS server for ${FRAME_READ_TIMEOUT_MS / 1000}s`,
        );
      }
      const r = result as ReadableStreamReadResult<Uint8Array>;
      if (r.done) return false;
      if (r.value) append(r.value);
      return true;
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  async function ensureBytes(n: number): Promise<boolean> {
    while (chunksLen < n) {
      if (signal?.aborted) return false;
      const got = await readMore();
      if (!got) return false; // EOF
    }
    return true;
  }

  try {
    while (true) {
      if (signal?.aborted) {
        throw new TtsStreamError("aborted", "Stream aborted by client");
      }

      // Read 5-byte header (1 tag + 4 length)
      const ok = await ensureBytes(5);
      if (!ok) {
        throw new TtsStreamError("truncated", "stream ended before END frame");
      }
      const hdr = consume(5);
      const tag = hdr[0];
      const len =
        ((hdr[1] << 24) >>> 0) +
        ((hdr[2] << 16) >>> 0) +
        ((hdr[3] << 8) >>> 0) +
        (hdr[4] >>> 0);

      if (len > MAX_FRAME_BYTES) {
        throw new TtsStreamError(
          "frame_too_large",
          `Frame size ${len} exceeds limit ${MAX_FRAME_BYTES}`,
        );
      }

      const payload = len === 0 ? new Uint8Array(0) : (await ensureBytes(len)) ? consume(len) : null;
      if (payload === null) {
        throw new TtsStreamError(
          "truncated",
          `stream ended mid-frame (tag=0x${tag.toString(16)}, expected ${len} bytes)`,
        );
      }

      if (tag === STREAM_TAG_CHUNK) {
        // payload is a fresh Uint8Array backed by a fresh ArrayBuffer (allocated
        // by consume()). Cast to Uint8Array<ArrayBuffer> so Blob accepts it
        // under TS's stricter Uint8Array generic.
        yield new Blob([payload as Uint8Array<ArrayBuffer>], { type: "audio/wav" });
      } else if (tag === STREAM_TAG_END) {
        return;
      } else if (tag === STREAM_TAG_ERROR) {
        let parsed: TtsStreamErrorPayload | null = null;
        try {
          parsed = JSON.parse(new TextDecoder().decode(payload)) as TtsStreamErrorPayload;
        } catch {
          // fall through with raw text
        }
        throw new TtsStreamError(
          parsed?.code ?? "stream_error",
          parsed?.message ?? new TextDecoder().decode(payload),
        );
      } else {
        throw new TtsStreamError("unknown_tag", `Unknown frame tag 0x${tag.toString(16)}`);
      }
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // already released
    }
    // Drain the underlying stream so the connection can close promptly.
    try {
      await resp.body?.cancel?.();
    } catch {
      // ignore
    }
  }
}

export async function unloadTts(): Promise<{ success: boolean }> {
  const base = engine.engineUrl;
  if (!base) throw new Error("Engine not discovered");
  return ttsJson<{ success: boolean }>(ttsUrl(base, "/unload"), {
    method: "DELETE",
  });
}

// ── Voice blending ────────────────────────────────────────────────────────────

export interface BlendComponent {
  voice_id: string;
  weight: number;
}

export interface ActionResponse {
  success: boolean;
  voice_id?: string;
  error?: string;
}

/**
 * Blend voices on the server and return a preview WAV blob (not saved).
 */
export async function blendPreview(
  components: BlendComponent[],
  speed = 1.0,
  lang = "en-us",
): Promise<{ blob: Blob; duration: number }> {
  const base = engine.engineUrl;
  if (!base) throw new Error("Engine not discovered");

  const auth = await authHeaders();
  const resp = await fetch(ttsUrl(base, "/blend/preview"), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...auth },
    body: JSON.stringify({ components, speed, lang }),
  });

  if (!resp.ok) {
    const detail = await resp.text().catch(() => resp.statusText);
    throw new Error(`Blend preview failed (${resp.status}): ${detail}`);
  }

  const blob = await resp.blob();
  const duration = parseFloat(resp.headers.get("X-TTS-Duration") ?? "0");
  return { blob, duration };
}

/**
 * Blend voices and save the result as a persistent custom voice.
 */
export async function saveBlendedVoice(params: {
  voice_id: string;
  name: string;
  components: BlendComponent[];
  gender?: string;
  lang_code?: string;
}): Promise<ActionResponse> {
  const base = engine.engineUrl;
  if (!base) throw new Error("Engine not discovered");
  return ttsJson<ActionResponse>(ttsUrl(base, "/blend/save"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
}

// ── Custom voice management ───────────────────────────────────────────────────

export interface CustomVoiceInfo {
  voice_id: string;
  name: string;
  gender: string;
  language: string;
  lang_code: string;
  quality_grade: string;
  traits: string[];
  is_custom: boolean;
  is_default: boolean;
  blend_recipe: BlendComponent[];
}

export async function listCustomVoices(): Promise<CustomVoiceInfo[]> {
  const base = engine.engineUrl;
  if (!base) throw new Error("Engine not discovered");
  return ttsJson<CustomVoiceInfo[]>(ttsUrl(base, "/custom-voices"));
}

export async function renameCustomVoice(
  voiceId: string,
  name: string,
): Promise<ActionResponse> {
  const base = engine.engineUrl;
  if (!base) throw new Error("Engine not discovered");
  return ttsJson<ActionResponse>(ttsUrl(base, `/custom-voices/${voiceId}`), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
}

export async function deleteCustomVoice(
  voiceId: string,
): Promise<ActionResponse> {
  const base = engine.engineUrl;
  if (!base) throw new Error("Engine not discovered");
  return ttsJson<ActionResponse>(ttsUrl(base, `/custom-voices/${voiceId}`), {
    method: "DELETE",
  });
}

/**
 * Import a custom voice from a .npy or .bin file.
 */
export async function importVoiceFile(params: {
  file: File;
  voice_id: string;
  name: string;
  gender?: string;
  lang_code?: string;
}): Promise<ActionResponse> {
  const base = engine.engineUrl;
  if (!base) throw new Error("Engine not discovered");

  const auth = await authHeaders();
  const form = new FormData();
  form.append("file", params.file);
  form.append("voice_id", params.voice_id);
  form.append("name", params.name);
  form.append("gender", params.gender ?? "female");
  form.append("lang_code", params.lang_code ?? "a");

  const resp = await fetch(ttsUrl(base, "/custom-voices/import"), {
    method: "POST",
    headers: auth,
    body: form,
  });

  if (!resp.ok) {
    const detail = await resp.text().catch(() => resp.statusText);
    throw new Error(`Voice import failed (${resp.status}): ${detail}`);
  }
  return resp.json();
}
