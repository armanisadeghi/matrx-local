import { invokeTauri, isTauri } from "@/lib/sidecar";

export type DurableErrorLevel = "warn" | "error";

export interface DurableErrorEvent {
  id: string;
  schemaVersion: 1;
  occurredAt: string;
  level: DurableErrorLevel;
  source: string;
  message: string;
  route: string;
  windowLabel: string;
  userId: string | null;
  organizationId: string | null;
}

export interface ErrorOutboxBridge {
  enqueue(event: DurableErrorEvent): Promise<void>;
  read(limit: number): Promise<DurableErrorEvent[]>;
  acknowledge(ids: string[]): Promise<void>;
}

/** Returns only event ids durably accepted by the platform. The uploader gets
 * one scan at a time so auth and organization state are resolved once. */
export type ErrorOutboxUploader = (
  events: DurableErrorEvent[],
) => Promise<string[]>;

export interface ErrorOutboxIdentity {
  userId: string;
  organizationId: string;
  accessToken: string;
}

export interface ErrorOutboxRpcResult {
  error: unknown | null;
}

export type ErrorOutboxRpc = (args: {
  p_source_app: "matrx-local";
  p_source: string;
  p_message: string;
  p_code: DurableErrorLevel;
  p_route?: string;
  p_context: {
    schema_version: 1;
    occurred_at: string;
    window_label: string;
  };
  p_organization_id: string;
}) => PromiseLike<ErrorOutboxRpcResult>;

const FLUSH_BATCH_SIZE = 20;
const FLUSH_SCAN_SIZE = 1_000;
const FLUSH_INTERVAL_MS = 30_000;

const nativeBridge: ErrorOutboxBridge = {
  enqueue: (event) =>
    invokeTauri<void>("enqueue_error_outbox_event", { event }),
  read: (limit) =>
    invokeTauri<DurableErrorEvent[]>("read_error_outbox_events", { limit }),
  acknowledge: (ids) =>
    invokeTauri<void>("acknowledge_error_outbox_events", { ids }),
};

const SENSITIVE_KEY_PATTERN =
  String.raw`(?:code|state|[A-Za-z0-9_.-]*(?:token|secret|password|passwd|credential|api[_-]?key|authorization|cookie)[A-Za-z0-9_.-]*|(?:key|[A-Za-z0-9_.-]*[_-]key(?:[_-][A-Za-z0-9_.-]*)?))`;

function isSensitiveKey(key: string): boolean {
  let decoded = key;
  try {
    decoded = decodeURIComponent(key);
  } catch {
    // A malformed encoded key cannot be trusted as non-sensitive.
  }
  const normalized = decoded.toLowerCase().replace(/[^a-z0-9]/g, "");
  const segments = decoded.toLowerCase().split(/[^a-z0-9]+/);
  return (
    normalized === "code" ||
    normalized === "state" ||
    segments.includes("key") ||
    [
      "token",
      "secret",
      "password",
      "passwd",
      "credential",
      "apikey",
      "authorization",
      "cookie",
    ].some((marker) => normalized.includes(marker))
  );
}

function redactDiagnosticText(value: string): string {
  const normalizedSeparators = value
    .replace(/%3A/gi, ":")
    .replace(/%3D/gi, "=")
    .replace(/%26/gi, "&")
    .replace(/%22/gi, '"');
  const encodedQuerySafe = normalizedSeparators.replace(
    /(^|[?&\s])((?:%[0-9A-Fa-f]{2}|[A-Za-z0-9_.-])+)=([^&\s]+)/g,
    (match, prefix: string, key: string) =>
      isSensitiveKey(key) ? `${prefix}${key}=[REDACTED]` : match,
  );
  return encodedQuerySafe
    .replace(
      /(\bauthorization\b\s*[:=]\s*(?:bearer|basic)\s+)[^\s,;}"']+/gi,
      "$1[REDACTED]",
    )
    .replace(/(bearer\s+)[^\s]+/gi, "$1[REDACTED]")
    .replace(
      new RegExp(`([?&]${SENSITIVE_KEY_PATTERN}=)[^&\\s]+`, "gi"),
      "$1[REDACTED]",
    )
    .replace(
      new RegExp(
        `(["']?${SENSITIVE_KEY_PATTERN}["']?\\s*[:=]\\s*)("[^"]*"|'[^']*'|[^\\s,;}]+)`,
        "gi",
      ),
      "$1[REDACTED]",
    )
    .replace(/(\b(?:cookie|set-cookie)\b\s*[:=]\s*)[^\r\n]+/gi, "$1[REDACTED]")
    .replace(/\bsk-[A-Za-z0-9_-]{8,}\b/g, "[REDACTED_API_KEY]")
    .replace(
      /\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b/g,
      "[REDACTED_JWT]",
    )
    .slice(0, 2_000);
}

function currentRoute(): string {
  if (typeof window === "undefined") return "";
  const hashRoute = window.location.hash.split("?")[0]?.split("#")[1];
  return (hashRoute || window.location.pathname || "").slice(0, 512);
}

function currentWindowLabel(): string {
  if (typeof window === "undefined") return "unknown";
  return (
    window as Window & {
      __TAURI_INTERNALS__?: {
        metadata?: { currentWebviewWindow?: { label?: string } };
      };
    }
  ).__TAURI_INTERNALS__?.metadata?.currentWebviewWindow?.label ?? "browser";
}

function eventId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

let captureContext: { userId: string; organizationId: string } | null = null;

/** Bind future events to the identity active when they occurred. Events
 * captured before this is known remain installation-local and are never
 * reassigned to whoever signs in next. */
export function setErrorOutboxCaptureContext(
  context: { userId: string; organizationId: string } | null,
): void {
  captureContext = context;
}

/** Coordinates asynchronous auth/org resolution. Starting a transition clears
 * capture synchronously; only the newest generation may publish context. */
export function createErrorOutboxIdentityCoordinator(
  publish: typeof setErrorOutboxCaptureContext = setErrorOutboxCaptureContext,
) {
  let generation = 0;

  function beginTransition(): number {
    generation += 1;
    publish(null);
    return generation;
  }

  function commit(
    expectedGeneration: number,
    context: { userId: string; organizationId: string } | null,
  ): boolean {
    if (expectedGeneration !== generation) return false;
    publish(context);
    return true;
  }

  return {
    beginTransition,
    commit,
    currentGeneration: () => generation,
    isCurrent: (expectedGeneration: number) =>
      expectedGeneration === generation,
  };
}

/** Upload one identity-homogeneous batch through an RPC already pinned to the
 * captured access token. Any intervening auth/org transition retains the
 * entire in-flight suffix instead of acknowledging under uncertain identity. */
export async function uploadIdentityBoundErrorBatch(
  events: DurableErrorEvent[],
  identity: ErrorOutboxIdentity,
  rpc: ErrorOutboxRpc,
  isIdentityCurrent: () => boolean,
): Promise<string[]> {
  const eligible = events
    .filter(
      (event) =>
        event.userId === identity.userId &&
        event.organizationId === identity.organizationId,
    )
    .slice(0, FLUSH_BATCH_SIZE);
  const acknowledged: string[] = [];

  for (const event of eligible) {
    if (!isIdentityCurrent()) break;
    const { error } = await rpc({
      p_source_app: "matrx-local",
      p_source: event.source,
      p_message: event.message,
      p_code: event.level,
      ...(event.route ? { p_route: event.route } : {}),
      p_context: {
        schema_version: event.schemaVersion,
        occurred_at: event.occurredAt,
        window_label: event.windowLabel,
      },
      p_organization_id: event.organizationId!,
    });
    if (error || !isIdentityCurrent()) break;
    acknowledged.push(event.id);
  }
  return acknowledged;
}

export function buildDurableErrorEvent(input: {
  level: DurableErrorLevel;
  message: string;
  source?: string;
}): DurableErrorEvent {
  return {
    id: eventId(),
    schemaVersion: 1,
    occurredAt: new Date().toISOString(),
    level: input.level,
    source: redactDiagnosticText(input.source || "renderer").slice(0, 128),
    message: redactDiagnosticText(input.message),
    route: currentRoute(),
    windowLabel: currentWindowLabel().slice(0, 128),
    userId: captureContext?.userId ?? null,
    organizationId: captureContext?.organizationId ?? null,
  };
}

export function createErrorOutboxController(
  bridge: ErrorOutboxBridge,
  getUploader: () => ErrorOutboxUploader | null,
) {
  let mutationChain = Promise.resolve();
  let flushing = false;

  function enqueue(event: DurableErrorEvent): void {
    mutationChain = mutationChain
      .then(() => bridge.enqueue(event))
      // The outbox cannot report its own write failure through itself. The
      // original line remains visible in Activity for this process.
      .catch(() => undefined);
  }

  async function flush(): Promise<void> {
    const uploader = getUploader();
    if (!uploader || flushing) return;
    flushing = true;
    try {
      await mutationChain;
      const events = await bridge.read(FLUSH_SCAN_SIZE);
      const eligibleIds = new Set(events.map((event) => event.id));
      const acknowledged = (await uploader(events))
        .filter((id) => eligibleIds.has(id))
        .slice(0, FLUSH_BATCH_SIZE);
      if (acknowledged.length > 0) {
        await bridge.acknowledge(acknowledged);
      }
    } catch {
      // Durable events remain queued. Never recurse through console/error log.
    } finally {
      flushing = false;
    }
  }

  return { enqueue, flush };
}

let uploader: ErrorOutboxUploader | null = null;
const controller = createErrorOutboxController(nativeBridge, () => uploader);
let flushTimer: ReturnType<typeof setInterval> | null = null;

export function enqueueDurableClientError(input: {
  level: DurableErrorLevel;
  message: string;
  source?: string;
}): void {
  if (!isTauri()) return;
  controller.enqueue(buildDurableErrorEvent(input));
  void controller.flush();
}

export function installErrorOutboxPersistence(
  nextUploader: ErrorOutboxUploader,
): () => void {
  uploader = nextUploader;
  void controller.flush();
  if (flushTimer === null) {
    flushTimer = setInterval(() => void controller.flush(), FLUSH_INTERVAL_MS);
  }
  return () => {
    uploader = null;
    if (flushTimer !== null) {
      clearInterval(flushTimer);
      flushTimer = null;
    }
  };
}
