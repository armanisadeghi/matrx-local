import { emitClientLog } from "@/hooks/use-unified-log";

export interface BackgroundTask {
  id: string;
  label: string;
  priority: number;
  runOnce?: boolean;
  fn: () => Promise<void>;
}

const SOURCE = "bg-tasks";

class BackgroundOrchestrator {
  private registry: BackgroundTask[] = [];
  private completed = new Set<string>();
  private running = false;
  private aborted = false;

  register(task: BackgroundTask): void {
    if (this.registry.some((t) => t.id === task.id)) return;
    this.registry.push(task);
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    this.aborted = false;
    this.completed.clear();
    this.drain();
  }

  stop(): void {
    this.aborted = true;
    this.running = false;
  }

  private drain(): void {
    const queue = [...this.registry]
      .filter((t) => {
        if (t.runOnce !== false && this.completed.has(t.id)) return false;
        return true;
      })
      .sort((a, b) => a.priority - b.priority);

    if (queue.length === 0) {
      this.running = false;
      return;
    }

    emitClientLog("info", `Background queue: ${queue.length} tasks`, SOURCE);

    const t0 = performance.now();
    let ran = 0;
    let skipped = 0;
    let failed = 0;

    const runNext = (idx: number) => {
      if (this.aborted || idx >= queue.length) {
        const elapsed = ((performance.now() - t0) / 1000).toFixed(1);
        emitClientLog(
          "success",
          `Queue complete: ${ran} tasks in ${elapsed}s (${skipped} skipped, ${failed} failed)`,
          SOURCE,
        );
        this.running = false;
        return;
      }

      const task = queue[idx];
      if (!task) {
        scheduleIdle(() => runNext(idx + 1));
        return;
      }
      if (task.runOnce !== false && this.completed.has(task.id)) {
        skipped++;
        scheduleIdle(() => runNext(idx + 1));
        return;
      }

      emitClientLog("info", `Starting: ${task.label}`, SOURCE);
      const taskStart = performance.now();

      task
        .fn()
        .then(() => {
          const ms = Math.round(performance.now() - taskStart);
          emitClientLog("info", `Done: ${task.label} (${ms}ms)`, SOURCE);
          this.completed.add(task.id);
          ran++;
        })
        .catch((err) => {
          const msg = err instanceof Error ? err.message : String(err);
          emitClientLog("error", `Failed: ${task.label} — ${msg}`, SOURCE);
          this.completed.add(task.id);
          failed++;
        })
        .finally(() => {
          scheduleIdle(() => runNext(idx + 1));
        });
    };

    scheduleIdle(() => runNext(0));
  }
}

function scheduleIdle(cb: () => void): void {
  if (typeof requestIdleCallback === "function") {
    requestIdleCallback(() => cb(), { timeout: 2000 });
  } else {
    setTimeout(cb, 50);
  }
}

export const orchestrator = new BackgroundOrchestrator();
