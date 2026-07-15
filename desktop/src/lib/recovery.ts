export type RecoveryLevel =
  | "refresh-surface"
  | "reset-surface"
  | "repair-service"
  | "reload-renderer"
  | "restart-engine"
  | "restart-app";
export type RecoveryStatus = "running" | "succeeded" | "failed" | "timed-out";

export interface RecoveryResult {
  ok: boolean;
  level: RecoveryLevel;
  target: string;
  message: string;
  startedAt: number;
  finishedAt: number;
  error?: string;
}
export interface RecoveryOperation extends Omit<RecoveryResult, "ok" | "finishedAt"> {
  id: string;
  status: RecoveryStatus;
  deadlineAt: number;
  finishedAt?: number;
}
export type SurfaceRecoveryHandler = () => void | Promise<void>;

const DEFAULT_TIMEOUT_MS = 15_000;
const MAX_HISTORY = 30;

class RecoveryService {
  private refreshHandlers = new Map<string, Set<SurfaceRecoveryHandler>>();
  private resetHandlers = new Map<string, Set<SurfaceRecoveryHandler>>();
  private operations: RecoveryOperation[] = [];
  private listeners = new Set<() => void>();
  private engineRestart: SurfaceRecoveryHandler | null = null;
  private snapshot: readonly RecoveryOperation[] = [];

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  getSnapshot = () => this.snapshot;
  private emit() {
    this.snapshot = [...this.operations];
    this.listeners.forEach((listener) => listener());
  }
  clearHistory = () => {
    this.operations = this.operations.filter((item) => item.status === "running");
    this.emit();
  };

  registerSurface(route: string, kind: "refresh" | "reset", handler: SurfaceRecoveryHandler) {
    const registry = kind === "refresh" ? this.refreshHandlers : this.resetHandlers;
    const handlers = registry.get(route) ?? new Set();
    handlers.add(handler);
    registry.set(route, handlers);
    return () => {
      handlers.delete(handler);
      if (handlers.size === 0) registry.delete(route);
    };
  }
  setEngineRestart(handler: SurfaceRecoveryHandler | null) {
    this.engineRestart = handler;
  }

  private async run(
    level: RecoveryLevel,
    target: string,
    action: SurfaceRecoveryHandler,
    timeoutMs = DEFAULT_TIMEOUT_MS,
  ): Promise<RecoveryResult> {
    const startedAt = Date.now();
    const operation: RecoveryOperation = {
      id: `${startedAt}-${Math.random().toString(36).slice(2)}`,
      level, target, startedAt, deadlineAt: startedAt + timeoutMs,
      status: "running", message: `Running ${level.replace(/-/g, " ")}`,
    };
    this.operations = [operation, ...this.operations].slice(0, MAX_HISTORY);
    this.emit();
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      await Promise.race([
        Promise.resolve().then(action),
        new Promise<never>((_, reject) => {
          timer = setTimeout(() => reject(new Error(`No response within ${Math.round(timeoutMs / 1000)} seconds`)), timeoutMs);
        }),
      ]);
      operation.status = "succeeded";
      operation.message = `${level.replace(/-/g, " ")} completed`;
      const finishedAt = Date.now();
      operation.finishedAt = finishedAt;
      return { ok: true, level, target, message: operation.message, startedAt, finishedAt };
    } catch (cause) {
      const error = cause instanceof Error ? cause.message : String(cause);
      operation.status = Date.now() >= operation.deadlineAt ? "timed-out" : "failed";
      operation.message = operation.status === "timed-out" ? "Operation became stale" : "Operation failed";
      operation.error = error;
      const finishedAt = Date.now();
      operation.finishedAt = finishedAt;
      return { ok: false, level, target, message: operation.message, error, startedAt, finishedAt };
    } finally {
      if (timer) clearTimeout(timer);
      this.emit();
    }
  }

  private matching(registry: Map<string, Set<SurfaceRecoveryHandler>>, route: string) {
    if (route !== "*") return [...(registry.get(route) ?? [])];
    return [...registry.values()].flatMap((handlers) => [...handlers]);
  }
  refreshSurface(route: string) {
    const handlers = this.matching(this.refreshHandlers, route);
    if (!handlers.length) return this.resetSurface(route, "No data refresh is registered; reset the view instead");
    return this.run("refresh-surface", route, async () => { await Promise.all(handlers.map((handler) => handler())); });
  }
  resetSurface(route: string, reason?: string) {
    const handlers = this.matching(this.resetHandlers, route);
    if (!handlers.length) {
      const now = Date.now();
      return Promise.resolve({ ok: false, level: "reset-surface" as const, target: route, message: reason ?? "This view does not support reset", error: "No reset handler registered", startedAt: now, finishedAt: now });
    }
    return this.run("reset-surface", route, async () => { await Promise.all(handlers.map((handler) => handler())); });
  }
  reloadRenderer() {
    return this.run("reload-renderer", "webview", async () => {
      const { isTauri, reloadRenderer } = await import("@/lib/sidecar");
      if (isTauri()) await reloadRenderer();
      else window.location.reload();
    }, 5_000);
  }
  restartEngine() {
    if (!this.engineRestart) {
      const now = Date.now();
      return Promise.resolve({ ok: false, level: "restart-engine" as const, target: "engine", message: "Engine restart is unavailable", error: "No restart handler registered", startedAt: now, finishedAt: now });
    }
    return this.run("restart-engine", "engine", this.engineRestart, 60_000);
  }
  repairService(service: string, action: string, handler: SurfaceRecoveryHandler) {
    return this.run("repair-service", `${service}.${action}`, handler, 120_000);
  }
  restartApp(reason = "user requested application recovery") {
    return this.run("restart-app", "application", async () => {
      const { restartApp } = await import("@/lib/sidecar");
      await restartApp(reason);
    }, 15_000);
  }
}

export const recovery = new RecoveryService();
