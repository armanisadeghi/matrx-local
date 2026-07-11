/**
 * use-unified-log — Singleton bus that owns ALL log data sources.
 *
 * Sources:
 *   "server"  → engine /setup/logs SSE (structured, history + live)
 *   "syslog"  → engine /logs/stream SSE (raw system.log tail)
 *   "access"  → engine /logs/access/stream SSE (structured HTTP requests)
 *   "tauri"   → Tauri IPC sidecar-log events (Rust ring buffer + live)
 *   "llm"     → Tauri llm-server-log events (llama-server stdout/stderr)
 *   "engine"  → engine discovery/connection lifecycle (emitted by use-engine)
 *   "auth"    → Supabase auth lifecycle (emitted by use-auth)
 *   "voice"   → transcription / whisper (emitted by voice hooks)
 *   "setup"   → setup wizard progress (emitted by SetupWizard)
 *   "bg-tasks" → background task orchestrator (token sync, cloud, prefetch)
 *
 * All streams self-initiate and auto-reconnect with exponential backoff.
 * The bus is module-level so it survives component re-renders and unmounts.
 *
 * Access log entries carry an extra `accessEntry` field for structured display.
 */

import { useEffect, useRef, useState } from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type LogLevel = "info" | "success" | "warn" | "error" | "data" | "cmd";

/** The structured data for an HTTP access log entry. */
export interface AccessEntry {
  timestamp: string;
  method: string;
  path: string;
  query: string;
  origin: string;
  user_agent: string;
  status: number;
  duration_ms: number;
}

export interface ClientLogLine {
  id: number;
  time: string;
  level: LogLevel;
  message: string;
  source?: string;
  /** Present only on source="access" lines — raw structured data for rich display. */
  accessEntry?: AccessEntry;
}

// ---------------------------------------------------------------------------
// Singleton bus & ring buffers
// ---------------------------------------------------------------------------

const _bus = new EventTarget();
const _EVENT = "client-log";
const _CLEAR_EVENT = "client-log-clear";
let _lineId = 0;

const MAX_TEXT_BUFFERED = 5000;
const MAX_ACCESS_BUFFERED = 1000;
const _buffer: ClientLogLine[] = [];

// Separate ring of raw access entries for consumers that want the full struct
const _accessBuffer: AccessEntry[] = [];

// ---------------------------------------------------------------------------
// Stream management
// ---------------------------------------------------------------------------

interface StreamState {
  engineUrl: string | null;
  getToken: (() => Promise<string | null>) | null;
  paused: boolean;
  // Stop functions for active streams
  stopSetupLogs: (() => void) | null;
  stopSyslog: (() => void) | null;
  stopAccess: (() => void) | null;
  stopTauri: (() => void) | null;
  stopLlm: (() => void) | null;
}

const _state: StreamState = {
  engineUrl: null,
  getToken: null,
  paused: false,
  stopSetupLogs: null,
  stopSyslog: null,
  stopAccess: null,
  stopTauri: null,
  stopLlm: null,
};

// Pause state change event so subscribers can react
const _PAUSE_EVENT = "unified-log-pause";

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function _makeTime(): string {
  return new Date().toLocaleTimeString("en-US", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function _push(line: ClientLogLine): void {
  _buffer.push(line);
  if (_buffer.length > MAX_TEXT_BUFFERED) {
    _buffer.splice(0, _buffer.length - MAX_TEXT_BUFFERED);
  }
  _bus.dispatchEvent(new CustomEvent(_EVENT, { detail: line }));
}

// ---------------------------------------------------------------------------
// Client-side dedup — suppresses repeated identical INFO messages within 10s.
// Warnings and errors are NEVER suppressed.
// ---------------------------------------------------------------------------

const _DEDUP_WINDOW_MS = 10_000;
const _dedupMap = new Map<string, number>();

function _isDuplicate(level: LogLevel, message: string): boolean {
  if (level === "warn" || level === "error") return false;
  const now = Date.now();
  const key = `${level}|${message}`;
  const prev = _dedupMap.get(key);
  if (prev !== undefined && now - prev < _DEDUP_WINDOW_MS) return true;
  _dedupMap.set(key, now);
  if (_dedupMap.size > 500) {
    const cutoff = now - _DEDUP_WINDOW_MS;
    for (const [k, ts] of _dedupMap) {
      if (ts < cutoff) _dedupMap.delete(k);
    }
  }
  return false;
}

/** Emit into the bus. Exported so external callers (auth, engine, voice, setup) can use it. */
export function emitClientLog(
  level: LogLevel,
  message: string,
  source?: string,
  accessEntry?: AccessEntry,
): void {
  if (_isDuplicate(level, message)) return;
  const line: ClientLogLine = {
    id: ++_lineId,
    time: _makeTime(),
    level,
    message,
    ...(source !== undefined ? { source } : {}),
    ...(accessEntry !== undefined ? { accessEntry } : {}),
  };
  _push(line);
}

export function getClientLogBuffer(): ClientLogLine[] {
  return [..._buffer];
}

export function getAccessBuffer(): AccessEntry[] {
  return [..._accessBuffer];
}

export function clearClientLog(): void {
  _buffer.splice(0, _buffer.length);
  _accessBuffer.splice(0, _accessBuffer.length);
  _bus.dispatchEvent(new CustomEvent(_CLEAR_EVENT, { detail: null }));
}

export function clearClientLogBySource(source: string): void {
  for (let i = _buffer.length - 1; i >= 0; i--) {
    if (_buffer[i].source === source) _buffer.splice(i, 1);
  }
  if (source === "access") {
    _accessBuffer.splice(0, _accessBuffer.length);
  }
  _bus.dispatchEvent(new CustomEvent(_CLEAR_EVENT, { detail: source }));
}

// ---------------------------------------------------------------------------
// Level inference helpers
// ---------------------------------------------------------------------------

// Strips ANSI escape codes so color-formatted console output from the Python
// sidecar doesn't prevent keyword matching (e.g. "\x1b[33mWARNING\x1b[0m").
const _ANSI_RE = /\x1b\[[0-9;]*m/g;

function stripAnsi(text: string): string {
  return text.replace(_ANSI_RE, "");
}

// ---------------------------------------------------------------------------
// Python logger format helpers (shared by tauri + syslog sources)
// ---------------------------------------------------------------------------

// Python standard logger format: "2026-05-08 12:17:51,635 - DEBUG - message"
// Matches the timestamp + level + separator prefix so it can be stripped.
const _PYTHON_LOG_PREFIX_RE =
  /^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}[,.\d]*\s+-\s+(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+-\s+/;

// Parse the log level from a raw Python logger line (timestamp+level+message).
// Returns null when the line is not in that format.
function parseSyslogLevel(msg: string): LogLevel | null {
  const m = msg.match(_PYTHON_LOG_PREFIX_RE);
  if (!m) return null;
  switch (m[1]) {
    case "ERROR":
    case "CRITICAL":
      return "error";
    case "WARNING":
      return "warn";
    default:
      return "info"; // INFO, DEBUG
  }
}

// Strip the Python logger timestamp+level prefix, leaving only the message body.
function cleanSyslogMessage(msg: string): string {
  return msg.replace(_PYTHON_LOG_PREFIX_RE, "").trim();
}

// Parses the embedded Python log level from raw tauri sidecar output.
// Lines look like: "[stdout] \x1b[33mWARNING\x1b[0m - message text"
// Returns null when the pattern is absent (non-Python or crash output).
function parseTauriLogLevel(rawText: string): LogLevel | null {
  let msg = stripAnsi(rawText);
  msg = msg.replace(/^\[std(?:out|err)\]\s*/, "");

  const m1 = msg.match(_PYTHON_LOG_PREFIX_RE);
  if (m1) {
    switch (m1[1]) {
      case "ERROR":
      case "CRITICAL":
        return "error";
      case "WARNING":
        return "warn";
      default:
        return "info";
    }
  }

  const m2 = msg.match(/^(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s*-/);
  if (!m2) return null;
  switch (m2[1]) {
    case "ERROR":
    case "CRITICAL":
      return "error";
    case "WARNING":
      return "warn";
    default:
      return "info"; // INFO, DEBUG
  }
}

// Cleans up a raw tauri sidecar log line for display:
//   1. Strips ANSI escape codes
//   2. Removes "[stdout] " / "[stderr] " prefix
//   3. Removes the redundant Python log-level prefix "WARNING - " etc.
function cleanTauriMessage(rawText: string): string {
  let msg = stripAnsi(rawText);
  msg = msg.replace(/^\[std(?:out|err)\]\s*/, "");

  if (_PYTHON_LOG_PREFIX_RE.test(msg)) {
    return cleanSyslogMessage(msg);
  }

  msg = msg.replace(/^(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s*-\s*/, "");
  return msg.trim();
}

function inferServerLevel(text: string): LogLevel {
  const t = stripAnsi(text).toLowerCase();
  // Hard error signals — always show
  if (
    t.includes("traceback") ||
    t.includes("exception") ||
    t.includes("critical") ||
    t.includes("fatal") ||
    t.includes("sigkill") ||
    t.includes("signal: some(9)") ||
    t.includes("terminated") ||
    t.includes("process exited")
  )
    return "error";

  const tForMatch = t.replace(/failed=(?:0|\[\])/g, "");

  if (tForMatch.includes("error") || tForMatch.includes("failed"))
    return "error";
  if (tForMatch.includes("warning") || tForMatch.includes("warn"))
    return "warn";
  if (
    t.includes("ready") ||
    t.includes("✓") ||
    t.includes("started") ||
    t.includes("success")
  )
    return "success";
  // Suppress DEBUG lines from the Python logger — they are high-volume and
  // contain internal timing details not useful for end-user troubleshooting.
  // They still go into the buffer (full history), just tagged as lowest priority.
  if (
    t.includes("debug") ||
    (t.includes("→") && !t.includes("error")) ||
    (t.includes("←") && !t.includes("error"))
  ) {
    // HTTP access lines from the request logger (→ GET, ← 200) — keep as "data"
    // level so the access tab can show them but the Server tab's "warn+" filter hides them.
    return "info";
  }
  return "info";
}

/** Map llama-server log kinds (from Rust classify_log_line) to unified levels. */
function llmKindToLevel(kind: string): LogLevel {
  if (kind === "error") return "error";
  if (kind === "ready") return "success";
  return "info";
}

function accessLevel(status: number): LogLevel {
  if (status >= 500) return "error";
  if (status >= 400) return "warn";
  return "data";
}

// ---------------------------------------------------------------------------
// Backoff reconnect helper
// ---------------------------------------------------------------------------

function makeBackoff(minMs = 1000, maxMs = 30000) {
  let delay = minMs;
  return {
    next(): number {
      const d = delay;
      delay = Math.min(delay * 2, maxMs);
      return d;
    },
    reset() {
      delay = minMs;
    },
  };
}

// ---------------------------------------------------------------------------
// /setup/logs stream (structured events: connected / log / history_end)
// ---------------------------------------------------------------------------

const SETUP_LOG_LEVEL_MAP: Record<string, LogLevel> = {
  debug: "info",
  info: "info",
  warning: "warn",
  error: "error",
  critical: "error",
};

function startSetupLogsStream(engineUrl: string): () => void {
  let active = true;
  let currentAbort: AbortController | null = null;
  const backoff = makeBackoff();

  const run = async () => {
    while (active) {
      // Create a fresh AbortController for each connection attempt so a
      // previous abort does not poison the next reconnect attempt.
      const abortCtrl = new AbortController();
      currentAbort = abortCtrl;

      try {
        const url = `${engineUrl}/setup/logs?lines=300`;
        const resp = await fetch(url, { signal: abortCtrl.signal });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

        backoff.reset();
        const reader = resp.body?.getReader();
        if (!reader) throw new Error("No response body");

        const decoder = new TextDecoder();
        let buf = "";
        let eventType = "";

        while (active) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          const lines = buf.split("\n");
          buf = lines.pop() ?? "";
          for (const raw of lines) {
            if (raw.startsWith("event: ")) {
              eventType = raw.slice(7).trim();
            } else if (raw.startsWith("data: ")) {
              try {
                const data = JSON.parse(raw.slice(6));
                if (eventType === "log") {
                  const d = data as { line: string; level: string };
                  emitClientLog(
                    SETUP_LOG_LEVEL_MAP[d.level] ?? "info",
                    d.line,
                    "server",
                  );
                } else if (eventType === "history_end") {
                  emitClientLog(
                    "info",
                    `── History (${data.lines_sent ?? 0} lines) ──────────────────────────`,
                    "server",
                  );
                } else if (eventType === "connected") {
                  emitClientLog(
                    "info",
                    `Connected — streaming from ${data.log_path ?? ""}`,
                    "server",
                  );
                }
              } catch {
                /* skip malformed */
              }
              eventType = "";
            }
          }
        }
      } catch (err) {
        if (!active) break;
        const msg = err instanceof Error ? err.message : String(err);
        // Suppress intentional aborts (from stop()) and network interruptions
        // that are expected when the engine restarts.
        if (!msg.includes("abort") && !msg.includes("AbortError")) {
          emitClientLog(
            "warn",
            `Server log stream reconnecting: ${msg}`,
            "server",
          );
        }
      }
      currentAbort = null;
      if (!active) break;
      const delay = backoff.next();
      await new Promise<void>((r) => setTimeout(r, delay));
    }
  };

  run();
  return () => {
    active = false;
    currentAbort?.abort();
    currentAbort = null;
  };
}

// ---------------------------------------------------------------------------
// /logs/stream stream (raw system.log tail via SSE)
// ---------------------------------------------------------------------------

async function startSyslogStream(
  engineUrl: string,
  getToken: () => Promise<string | null>,
): Promise<() => void> {
  let active = true;
  let esRef: EventSource | null = null;
  const backoff = makeBackoff();
  let noTokenWarned = false;

  const connect = async () => {
    if (!active) return;
    const token = await getToken();
    if (!active) return;
    if (!token) {
      // Warn once so the user knows why detailed server logs are missing.
      if (!noTokenWarned) {
        noTokenWarned = true;
        emitClientLog(
          "warn",
          "Activity: detailed server log stream requires authentication — some warnings and errors may not appear until you sign in.",
          "syslog",
        );
      }
      // Retry after a longer delay — the user may sign in shortly after engine connects.
      setTimeout(connect, 10_000);
      return;
    }
    // Token obtained — reset the no-token warning so it shows again if session lapses.
    noTokenWarned = false;
    const url = `${engineUrl}/logs/stream?token=${encodeURIComponent(token)}`;
    const es = new EventSource(url);
    esRef = es;

    es.onmessage = (evt) => {
      if (_state.paused) return;
      const raw = stripAnsi(evt.data);
      const level = parseSyslogLevel(raw) ?? inferServerLevel(raw);
      const msg = cleanSyslogMessage(raw);
      emitClientLog(level, msg, "syslog");
    };

    es.onerror = () => {
      es.close();
      esRef = null;
      if (!active) return;
      const delay = backoff.next();
      setTimeout(connect, delay);
    };

    es.onopen = () => {
      backoff.reset();
    };
  };

  connect();

  return () => {
    active = false;
    esRef?.close();
    esRef = null;
  };
}

// ---------------------------------------------------------------------------
// Tauri llm-server-log IPC  (llama-server stdout/stderr → Activity)
// ---------------------------------------------------------------------------

async function startLlmStream(): Promise<() => void> {
  let unlistenFn: (() => void) | null = null;

  try {
    const { listen } = await import("@tauri-apps/api/event");
    const unlisten = await listen<{ line: string; kind: string }>(
      "llm-server-log",
      (event) => {
        if (_state.paused) return;
        const { line, kind } = event.payload;
        // Prepend a source tag so it's visually distinct in the Activity list.
        emitClientLog(llmKindToLevel(kind), `[llama-server] ${line}`, "llm");
      },
    );
    unlistenFn = unlisten;
  } catch {
    /* not in Tauri — browser dev mode */
  }

  return () => {
    unlistenFn?.();
  };
}

// ---------------------------------------------------------------------------
// /logs/access snapshot + /logs/access/stream SSE
// ---------------------------------------------------------------------------

async function startAccessStream(
  engineUrl: string,
  getToken: () => Promise<string | null>,
): Promise<() => void> {
  let active = true;
  let esRef: EventSource | null = null;
  const backoff = makeBackoff();

  // Fetch historical snapshot first
  const token = await getToken();
  if (token && active) {
    try {
      const resp = await fetch(`${engineUrl}/logs/access?n=200`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (resp.ok) {
        const data = (await resp.json()) as { entries?: AccessEntry[] };
        if (Array.isArray(data?.entries)) {
          const entries = data.entries.slice(-200);
          entries.forEach((entry) => {
            _accessBuffer.push(entry);
            const msg = `${entry.method} ${entry.path}${entry.query ? `?${entry.query}` : ""} → ${entry.status} (${entry.duration_ms.toFixed(0)}ms)`;
            emitClientLog(accessLevel(entry.status), msg, "access", entry);
          });
          if (_accessBuffer.length > MAX_ACCESS_BUFFERED) {
            _accessBuffer.splice(0, _accessBuffer.length - MAX_ACCESS_BUFFERED);
          }
        }
      }
    } catch {
      /* silent */
    }
  }

  const connect = async () => {
    if (!active) return;
    const tok = await getToken();
    if (!active) return;
    if (!tok) {
      // No session yet — retry after a longer delay (the user may sign in
      // shortly after the engine connects). Mirrors the syslog stream.
      setTimeout(connect, 10_000);
      return;
    }
    const url = `${engineUrl}/logs/access/stream?token=${encodeURIComponent(tok)}`;
    const es = new EventSource(url);
    esRef = es;

    es.onmessage = (evt) => {
      if (_state.paused) return;
      try {
        const entry: AccessEntry = JSON.parse(evt.data);
        _accessBuffer.push(entry);
        if (_accessBuffer.length > MAX_ACCESS_BUFFERED) {
          _accessBuffer.splice(0, _accessBuffer.length - MAX_ACCESS_BUFFERED);
        }
        const msg = `${entry.method} ${entry.path}${entry.query ? `?${entry.query}` : ""} → ${entry.status} (${entry.duration_ms.toFixed(0)}ms)`;
        emitClientLog(accessLevel(entry.status), msg, "access", entry);
      } catch {
        /* skip malformed */
      }
    };

    es.onerror = () => {
      es.close();
      esRef = null;
      if (!active) return;
      setTimeout(connect, backoff.next());
    };

    es.onopen = () => {
      backoff.reset();
    };
  };

  connect();

  return () => {
    active = false;
    esRef?.close();
    esRef = null;
  };
}

// ---------------------------------------------------------------------------
// Tauri sidecar-log IPC
// ---------------------------------------------------------------------------

// Recent tauri log lines for error context burst (last N lines before a crash)
const _recentTauriLines: string[] = [];
const RECENT_CONTEXT_SIZE = 50;

function _trackRecentLine(text: string): void {
  _recentTauriLines.push(text);
  if (_recentTauriLines.length > RECENT_CONTEXT_SIZE) {
    _recentTauriLines.shift();
  }
}

function _isCrashSignal(text: string): boolean {
  const stripped = stripAnsi(text);

  // Lines from the preflight and launcher subsystems are by-design
  // informational — those modules exist specifically to TERMINATE / SIGKILL
  // stale processes (preflight) and STOP children during shutdown
  // (launcher), and they log the actions they take. Without this guard,
  // normal output ("✓ pid X terminated", "tunnel → stopping",
  // "scraper → ✓ stopped", "cloudflared exit code: 1") tripped
  // multiple crash-keyword matches on every clean startup AND every clean
  // shutdown, dumping spurious "[CRASH DETECTED]" + 50-line context bursts
  // to the log even though the engine was perfectly healthy. The detector
  // below is meant to catch a SIDECAR dying unexpectedly, not its lifecycle
  // modules doing their job.
  //
  // [cloudflared] is also excluded — when the tunnel exits with code 1 we
  // log the recent output prefixed with [cloudflared] so the operator can
  // see why; those lines often include "error", "failed", "abort" from
  // cloudflared's own logging and are NOT a sidecar crash.
  if (
    stripped.includes("[preflight]") ||
    stripped.includes("[launcher]") ||
    stripped.includes("[cloudflared]")
  ) {
    return false;
  }

  const t = stripped.toLowerCase();
  return (
    t.includes("process exited") ||
    t.includes("terminated") ||
    t.includes("signal: some(9)") ||
    t.includes("sigkill") ||
    t.includes("panic!") ||
    (t.includes("signal:") && t.includes("some("))
  );
}

async function startTauriStream(): Promise<() => void> {
  let unlistenFn: (() => void) | null = null;

  try {
    const { invoke } = await import("@tauri-apps/api/core");
    const historical = await invoke<string[]>("get_sidecar_logs").catch(
      () => [] as string[],
    );
    historical.forEach((text) => {
      _trackRecentLine(text);
      emitClientLog(
        parseTauriLogLevel(text) ?? inferServerLevel(text),
        cleanTauriMessage(text),
        "tauri",
      );
    });
  } catch {
    /* not in Tauri */
  }

  try {
    const { listen } = await import("@tauri-apps/api/event");
    const unlisten = await listen<string>("sidecar-log", (event) => {
      if (_state.paused) return;
      const text =
        typeof event.payload === "string"
          ? event.payload
          : String(event.payload);
      _trackRecentLine(text);

      // If this line is a crash signal, immediately dump recent context
      if (_isCrashSignal(text)) {
        emitClientLog(
          "error",
          `[CRASH DETECTED] ${cleanTauriMessage(text)}`,
          "tauri",
        );
        emitClientLog(
          "error",
          `━━━ Last ${_recentTauriLines.length} engine lines before crash ━━━`,
          "tauri",
        );
        [..._recentTauriLines].forEach((line) => {
          emitClientLog("error", `  ${cleanTauriMessage(line)}`, "tauri");
        });
        emitClientLog(
          "error",
          `━━━ End of crash context ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`,
          "tauri",
        );
      } else {
        emitClientLog(
          parseTauriLogLevel(text) ?? inferServerLevel(text),
          cleanTauriMessage(text),
          "tauri",
        );
      }
    });
    unlistenFn = unlisten;
  } catch {
    /* not in Tauri */
  }

  return () => {
    unlistenFn?.();
  };
}

// ---------------------------------------------------------------------------
// React console capture
// ---------------------------------------------------------------------------

let _consoleRestoreFn: (() => void) | null = null;

/**
 * Override browser console methods to forward output into the unified log bus
 * as source="react" entries. Call once at app startup (idempotent).
 *
 * console.warn / console.error  → level "warn" / "error"
 * console.log / console.info / console.debug → level "info"
 *
 * Returns a restore function that uninstalls the overrides.
 */
export function initConsoleCapture(): () => void {
  if (_consoleRestoreFn) return _consoleRestoreFn;

  const origLog = console.log.bind(console);
  const origInfo = console.info.bind(console);
  const origDebug = console.debug.bind(console);
  const origWarn = console.warn.bind(console);
  const origError = console.error.bind(console);

  const toMsg = (...args: unknown[]): string =>
    args.map((a) => (typeof a === "string" ? a : JSON.stringify(a))).join(" ");

  console.log = (...a: unknown[]) => {
    origLog(...a);
    emitClientLog("info", toMsg(...a), "react");
  };
  console.info = (...a: unknown[]) => {
    origInfo(...a);
    emitClientLog("info", toMsg(...a), "react");
  };
  console.debug = (...a: unknown[]) => {
    origDebug(...a);
    emitClientLog("info", toMsg(...a), "react");
  };
  console.warn = (...a: unknown[]) => {
    origWarn(...a);
    emitClientLog("warn", toMsg(...a), "react");
  };
  console.error = (...a: unknown[]) => {
    origError(...a);
    emitClientLog("error", toMsg(...a), "react");
  };

  _consoleRestoreFn = () => {
    console.log = origLog;
    console.info = origInfo;
    console.debug = origDebug;
    console.warn = origWarn;
    console.error = origError;
    _consoleRestoreFn = null;
  };

  return _consoleRestoreFn;
}

// ---------------------------------------------------------------------------
// Public API — init / pause / teardown
// ---------------------------------------------------------------------------

/**
 * Initialize all log streams. Call once when engineUrl becomes available.
 * Safe to call multiple times — only starts new streams if engineUrl changed.
 */
export async function initUnifiedLog(
  engineUrl: string,
  getToken: () => Promise<string | null>,
): Promise<void> {
  // Stop existing engine streams if URL changed
  if (_state.engineUrl !== engineUrl) {
    _state.stopSetupLogs?.();
    _state.stopSyslog?.();
    _state.stopAccess?.();
    _state.stopSetupLogs = null;
    _state.stopSyslog = null;
    _state.stopAccess = null;
  }

  _state.engineUrl = engineUrl;
  _state.getToken = getToken;

  if (!_state.stopSetupLogs) {
    _state.stopSetupLogs = startSetupLogsStream(engineUrl);
  }
  if (!_state.stopSyslog) {
    _state.stopSyslog = await startSyslogStream(engineUrl, getToken);
  }
  if (!_state.stopAccess) {
    _state.stopAccess = await startAccessStream(engineUrl, getToken);
  }
}

/**
 * Initialize the Tauri sidecar listener and llm-server-log listener.
 * Call once on app mount (no engine required).
 */
export async function initTauriLogStream(): Promise<void> {
  if (!_state.stopTauri) {
    _state.stopTauri = await startTauriStream();
  }
  if (!_state.stopLlm) {
    _state.stopLlm = await startLlmStream();
  }
}

/**
 * Stop all engine streams (e.g. when engine disconnects).
 */
export function stopEngineStreams(): void {
  _state.stopSetupLogs?.();
  _state.stopSyslog?.();
  _state.stopAccess?.();
  _state.stopSetupLogs = null;
  _state.stopSyslog = null;
  _state.stopAccess = null;
  _state.engineUrl = null;
}

/**
 * Stop the Tauri sidecar log listener and the llm-server-log listener.
 * Call on app unmount to remove Tauri IPC event listeners.
 */
export function stopTauriStream(): void {
  _state.stopTauri?.();
  _state.stopTauri = null;
  _state.stopLlm?.();
  _state.stopLlm = null;
}

/**
 * Stop all streams (both engine and Tauri). Call on full app teardown.
 */
export function stopAllStreams(): void {
  stopEngineStreams();
  stopTauriStream();
}

/**
 * Get / set the global pause state. When paused, live incoming SSE events are
 * dropped (historical buffer is unaffected).
 */
export function setLogsPaused(paused: boolean): void {
  _state.paused = paused;
  _bus.dispatchEvent(new CustomEvent(_PAUSE_EVENT, { detail: paused }));
}

export function getLogsPaused(): boolean {
  return _state.paused;
}

// ---------------------------------------------------------------------------
// React hooks
// ---------------------------------------------------------------------------

/**
 * Subscribe to all log lines. Initialises with the full historical buffer.
 */
export function useClientLogSubscriber(): ClientLogLine[] {
  const [lines, setLines] = useState<ClientLogLine[]>(() =>
    getClientLogBuffer(),
  );
  const setLinesRef = useRef(setLines);
  setLinesRef.current = setLines;

  useEffect(() => {
    const onLine = (e: Event) => {
      const line = (e as CustomEvent<ClientLogLine>).detail;
      setLinesRef.current((prev) => {
        const next = [...prev, line];
        return next.length > MAX_TEXT_BUFFERED
          ? next.slice(next.length - MAX_TEXT_BUFFERED)
          : next;
      });
    };

    const onClear = (e: Event) => {
      const source = (e as CustomEvent<string | null>).detail;
      if (source == null) {
        setLinesRef.current([]);
      } else {
        setLinesRef.current((prev) => prev.filter((l) => l.source !== source));
      }
    };

    _bus.addEventListener(_EVENT, onLine);
    _bus.addEventListener(_CLEAR_EVENT, onClear);
    return () => {
      _bus.removeEventListener(_EVENT, onLine);
      _bus.removeEventListener(_CLEAR_EVENT, onClear);
    };
  }, []);

  return lines;
}

/**
 * Subscribe to just the pause/resume state.
 */
export function useLogsPaused(): boolean {
  const [paused, setPaused] = useState(() => getLogsPaused());

  useEffect(() => {
    const handler = (e: Event) => {
      setPaused((e as CustomEvent<boolean>).detail);
    };
    _bus.addEventListener(_PAUSE_EVENT, handler);
    return () => _bus.removeEventListener(_PAUSE_EVENT, handler);
  }, []);

  return paused;
}
