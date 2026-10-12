// The desktop's typed engine errors, and the ONE place `@ai-matrx/api`'s
// `MatrxApiError` becomes one of them.
//
// What a person reads is pinned in `engine-errors.test.ts`: before the engine
// calls rode the shared client, each EngineAPI method owned its own sentence
// ("Failed to get settings: 500"); those sentences live in LEGACY_LABELS below
// so the move changed the plumbing and not the words.

import { MatrxApiError, NetworkError } from "@ai-matrx/api";

export const ENGINE_NOT_DISCOVERED = "Engine not discovered";

/** The engine has no address yet (never discovered, or lost). */
export class EngineUnreachableError extends Error {
  constructor(message: string = ENGINE_NOT_DISCOVERED) {
    super(message);
    this.name = "EngineUnreachableError";
  }
}

/** The engine answered with a non-2xx status. */
export class EngineHttpError extends Error {
  readonly status: number;
  readonly method: string;
  readonly path: string;
  readonly code: string | null;
  readonly serverDetail: unknown;
  /** The server's own words for the failure (no method/path framing). */
  readonly detail: string;
  constructor(
    message: string,
    init: {
      status: number;
      method: string;
      path: string;
      code: string | null;
      serverDetail: unknown;
      detail: string;
    },
  ) {
    super(message);
    this.name = "EngineHttpError";
    this.detail = init.detail;
    this.status = init.status;
    this.method = init.method;
    this.path = init.path;
    this.code = init.code;
    this.serverDetail = init.serverDetail;
  }
}

/** 404 — the route or the record is not there. */
export class EngineNotFoundError extends EngineHttpError {
  constructor(message: string, init: ConstructorParameters<typeof EngineHttpError>[1]) {
    super(message, init);
    this.name = "EngineNotFoundError";
  }
}

/** 5xx — the engine itself failed. */
export class EngineServerError extends EngineHttpError {
  constructor(message: string, init: ConstructorParameters<typeof EngineHttpError>[1]) {
    super(message, init);
    this.name = "EngineServerError";
  }
}

interface LegacyLabel {
  label: string;
  /** The old sentence carried the response body after the status. */
  withBody?: boolean;
  /** The old sentence carried the response body INSTEAD of the status. */
  bodyOnly?: boolean;
}

/**
 * "METHOD /path" (a `{x}` segment matches any single segment) -> the sentence
 * the method showed before it rode the shared client. Sentence shape is
 * `${label}: ${status}`.
 */
const LEGACY_LABELS: ReadonlyArray<readonly [string, LegacyLabel]> = [
  ["GET /health", { label: "Health check failed" }],
  ["GET /settings", { label: "Failed to get settings" }],
  ["PUT /settings", { label: "Failed to update settings" }],
  ["PUT /settings/paths/{name}", { label: "Failed to set path", bodyOnly: true }],
  ["DELETE /settings/paths/{name}", { label: "Failed to reset path" }],
  ["GET /chat/ai-status", { label: "Failed to get AI status" }],
  ["GET /wake-word/status", { label: "OWW status failed" }],
  ["GET /wake-word/models", { label: "OWW list models failed" }],
  ["GET /remote-scraper/status", { label: "Remote scraper status failed" }],
  ["POST /remote-scraper/search", { label: "Remote search failed" }],
  ["POST /remote-scraper/research", { label: "Remote research failed" }],
  ["POST /remote-scraper/content/save", { label: "Content save failed" }],
  ["GET /remote-scraper/queue/stats", { label: "Queue stats failed" }],
  ["GET /remote-scraper/queue/pending", { label: "Queue pending failed" }],
  ["GET /egress/status", { label: "Home connection status failed" }],
  ["POST /egress/{action}", { label: "Home connection failed" }],
  ["GET /cloud/settings", { label: "Cloud settings failed" }],
  ["PUT /cloud/settings", { label: "Cloud settings update failed" }],
  ["POST /cloud/sync", { label: "Cloud sync failed" }],
  ["POST /cloud/sync/push", { label: "Cloud push failed" }],
  ["POST /cloud/sync/pull", { label: "Cloud pull failed" }],
  ["GET /cloud/instance", { label: "Instance info failed" }],
  ["GET /cloud/instances", { label: "List instances failed" }],
  ["GET /devices/permissions/{name}", { label: "Permission check failed" }],
  ["GET /setup/debug", { label: "Debug state failed" }],
  ["POST /wake-word/start", { label: "OWW start failed", withBody: true }],
  ["POST /wake-word/stop", { label: "OWW stop failed" }],
  ["POST /wake-word/mute", { label: "OWW mute failed" }],
  ["POST /wake-word/unmute", { label: "OWW unmute failed" }],
  ["POST /wake-word/dismiss", { label: "OWW dismiss failed" }],
  ["POST /wake-word/trigger", { label: "OWW trigger failed" }],
  ["POST /wake-word/configure", { label: "OWW configure failed" }],
  ["POST /wake-word/models/download", { label: "OWW download failed", withBody: true }],
  ["GET /devices/audio", { label: "Audio device check failed" }],
  ["GET /devices/bluetooth", { label: "Bluetooth check failed" }],
  ["GET /devices/wifi", { label: "WiFi scan failed" }],
  ["GET /devices/network", { label: "Network info failed" }],
  ["GET /devices/connected", { label: "Connected devices check failed" }],
  ["GET /devices/system", { label: "System resources check failed" }],
  ["GET /devices/camera", { label: "Camera probe failed" }],
  ["GET /devices/screens", { label: "Screens probe failed" }],
  ["GET /devices/screenshot", { label: "Screenshot failed" }],
  ["GET /devices/location", { label: "Location probe failed" }],
  ["POST /devices/record-audio", { label: "Audio recording failed" }],
  ["POST /devices/capture-photo", { label: "Photo capture failed" }],
  ["POST /devices/record-video", { label: "Video recording failed" }],
  ["POST /devices/record-screen", { label: "Screen recording failed" }],
  ["GET /tools/list", { label: "Failed to list tools" }],
  ["GET /chat/tools", { label: "Failed to get tool schemas" }],
  ["GET /chat/tools/by-category", { label: "Failed to get tool schemas" }],
  ["POST /remote-scraper/scrape", { label: "Remote scrape failed", withBody: true }],
  ["POST /remote-scraper/search-and-scrape", { label: "Remote search-and-scrape failed" }],
  ["GET /remote-scraper/queue/poller-stats", { label: "Poller stats failed" }],
  ["GET /chat/local-llm/status", { label: "Failed to get local LLM status" }],
  ["POST /chat/local-llm/connect", { label: "Failed to connect local LLM" }],
  ["POST /chat/local-llm/disconnect", { label: "Failed to disconnect local LLM" }],
  ["GET /settings/wake-word", { label: "Failed to get wake word settings" }],
  ["PUT /settings/wake-word", { label: "Failed to save wake word settings" }],
];

function templateRegex(template: string): RegExp {
  const escaped = template.replace(/[.*+?^$()|[\]\\]/g, "\\$&");
  return new RegExp(`^${escaped.replace(/\{[^}]+\}/g, "[^/]+")}$`);
}

const LEGACY_MATCHERS = LEGACY_LABELS.map(
  ([key, value]) => [key.split(" ", 2) as [string, string], templateRegex(key.split(" ", 2)[1]!), value] as const,
);

function legacyLabel(method: string, path: string): LegacyLabel | undefined {
  const pathname = path.split("?")[0] ?? path;
  const m = method.toUpperCase();
  for (const [[lm], regex, value] of LEGACY_MATCHERS) {
    if (lm === m && regex.test(pathname)) return value;
  }
  return undefined;
}

/** Render a server `detail` / `message` value as one sentence. */
export type DetailRenderer = (detail: unknown, fallback: string) => string;

/**
 * The ONE mapping from the shared client's error to what the desktop throws.
 * A labelled route says what its method always said; any other route keeps the
 * `METHOD path failed: <detail>` sentence plus hint and diagnostic reference.
 */
export function toEngineError(
  error: unknown,
  method: string,
  path: string,
  renderDetail: DetailRenderer,
): unknown {
  // A refused connection keeps the transport's own words ("Failed to fetch").
  if (error instanceof NetworkError) return new EngineUnreachableError(error.message);
  if (!(error instanceof MatrxApiError)) return error;
  const init = {
    status: error.status,
    method,
    path,
    code: error.code,
    serverDetail: error.serverDetail,
    detail: "",
  };
  const body =
    error.serverDetail && typeof error.serverDetail === "object"
      ? (error.serverDetail as Record<string, unknown>)
      : null;
  const labelled = legacyLabel(method, path);
  init.detail = renderDetail(body?.message ?? body?.detail, "");
  let message: string;
  if (labelled) {
    const text = renderDetail(body?.message ?? body?.detail, "");
    const tail = labelled.bodyOnly
      ? text || String(error.status)
      : labelled.withBody && text
        ? `${error.status} ${text}`
        : String(error.status);
    message = `${labelled.label}: ${tail}`;
  } else {
    const detail = renderDetail(body?.message ?? body?.detail, `HTTP ${error.status}`);
    const hint = typeof body?.hint === "string" ? body.hint : "";
    const reference =
      typeof body?.failure_id === "string"
        ? `Diagnostic reference: ${body.failure_id}.`
        : "";
    message = [`${method} ${path} failed: ${detail}`, hint, reference]
      .filter(Boolean)
      .join(" ");
  }
  if (error.status === 404) return new EngineNotFoundError(message, init);
  if (error.status >= 500) return new EngineServerError(message, init);
  return new EngineHttpError(message, init);
}
