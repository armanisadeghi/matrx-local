/**
 * The engine supervisor's live state, as the Rust side sees it.
 *
 * Rust owns the engine process, so Rust owns the truth about a failed start:
 * it counts the attempts, names the cause from the engine's own stderr, and
 * stops at a bound (`lib.rs`, `supervise_terminated_engine`). This module is
 * the read side — an event subscription plus a snapshot read for a window that
 * mounts mid-incident — and nothing here decides anything.
 *
 * Why it exists at all: the engine used to die at spawn and STAY dead, with
 * the app showing only a passive "engine is not running" banner and a manual
 * button (row SR-02 of
 * `common-docs/projects/coding-agent-bridge/audit-2026-09-14-engine-self-repair.md`
 * measured an 11-minute outage that way). An automatic recovery the user
 * cannot see is just as bad, so every attempt announces itself.
 */

import { invokeTauri, isTauri } from "@/lib/sidecar";
import { enqueueDurableClientError } from "@/lib/error-outbox";

/** `ok` — nothing wrong. `restarting` — an automatic attempt is in flight.
 *  `failed` — the bound is spent and the user has to act. */
export type EngineSupervisorPhase = "ok" | "restarting" | "failed";

export interface EngineSupervisorStatus {
  phase: EngineSupervisorPhase;
  attempt: number;
  maxAttempts: number;
  /** The engine's own words, present for `restarting` and `failed`. */
  cause: string | null;
  /** What the app is doing about it, in plain English. */
  remedy: string | null;
  exitCode: number | null;
  exitSignal: number | null;
  updatedAtMs: number;
}

export const ENGINE_SUPERVISOR_OK: EngineSupervisorStatus = {
  phase: "ok",
  attempt: 0,
  maxAttempts: 0,
  cause: null,
  remedy: null,
  exitCode: null,
  exitSignal: null,
  updatedAtMs: 0,
};

/**
 * The one sentence the user reads. Never "something went wrong": it carries
 * the cause and, while retrying, which attempt is running.
 */
export function engineSupervisorHeadline(status: EngineSupervisorStatus): string {
  const cause = status.cause?.trim();
  const because = cause ? `: ${cause}` : "";
  if (status.phase === "restarting") {
    return `Engine failed to start${because} — restarting (${status.attempt}/${status.maxAttempts})`;
  }
  if (status.phase === "failed") {
    return `Engine failed to start${because} — automatic restarts gave up after ${status.attempt} ${
      status.attempt === 1 ? "try" : "tries"
    }`;
  }
  return "The local engine is running";
}

export class EngineSupervisorFeed {
  private status: EngineSupervisorStatus = ENGINE_SUPERVISOR_OK;
  private listeners = new Set<() => void>();
  private unlisten: (() => void) | null = null;
  private started = false;
  private pendingIncidentCapture: Parameters<typeof enqueueDurableClientError>[0] | null = null;
  private incidentCaptured = false;

  constructor(
    private readonly capture: typeof enqueueDurableClientError = enqueueDurableClientError,
  ) {}

  getSnapshot = () => this.status;

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    void this.start();
    return () => {
      this.listeners.delete(listener);
    };
  };

  /** Test seam and event sink — replaces the snapshot and wakes subscribers. */
  push = (status: EngineSupervisorStatus) => {
    const opensIncident =
      this.status.phase === "ok" && status.phase !== "ok";
    if (opensIncident) {
      const exitCode = status.exitCode ?? "none";
      const exitSignal = status.exitSignal ?? "none";
      this.pendingIncidentCapture = {
        level: "error",
        source: "engine-supervisor",
        message: `The local engine exited unexpectedly; automatic recovery entered ${status.phase} (exit code ${exitCode}, signal ${exitSignal}).`,
        causalSignature: `engine-supervisor:${status.phase}:exit-${exitCode}:signal-${exitSignal}`,
        requireIdentity: true,
      };
      this.incidentCaptured = false;
    }
    if (status.phase === "ok") {
      this.pendingIncidentCapture = null;
      this.incidentCaptured = false;
    } else if (this.pendingIncidentCapture && !this.incidentCaptured) {
      this.incidentCaptured = this.capture(this.pendingIncidentCapture);
    }
    this.status = status;
    this.listeners.forEach((listener) => listener());
  };

  private async start() {
    if (this.started || !isTauri()) return;
    this.started = true;
    try {
      // Read first: a window that opens mid-incident must not show "ok" until
      // the next event happens to arrive.
      this.push(await invokeTauri<EngineSupervisorStatus>("engine_supervisor_status"));
    } catch {
      // A desktop build without the command is simply silent here — the
      // EngineDownBanner still covers a down engine.
    }
    try {
      const { listen } = await import("@tauri-apps/api/event");
      this.unlisten = await listen<EngineSupervisorStatus>("engine-supervisor", (event) => {
        this.push(event.payload);
      });
    } catch {
      this.started = false;
    }
  }

  /** Only tests and teardown need this. */
  stop = () => {
    this.unlisten?.();
    this.unlisten = null;
    this.started = false;
  };
}

export const engineSupervisor = new EngineSupervisorFeed();
