import { useState, useCallback, useRef, useEffect, useMemo } from "react";
import { engine } from "@/lib/api";

interface ExecutionEntry {
  id: string;
  toolName: string;
  params: Record<string, unknown>;
  startedAt: Date;
  completedAt?: Date;
  elapsedMs?: number;
  result?: unknown;
  error?: string;
  status: "running" | "success" | "error";
}

interface UseToolExecutionReturn {
  /** Current execution state */
  loading: boolean;
  result: unknown | null;
  error: string | null;
  /** Execution history (most recent first) */
  history: ExecutionEntry[];
  /** Elapsed time of current execution in ms */
  elapsedMs: number;
  /** Invoke a tool */
  invoke: (toolName: string, params: Record<string, unknown>) => Promise<void>;
  /** Abort the current execution */
  abort: () => void;
  /** Reset current result/error */
  reset: () => void;
  /** Clear history */
  clearHistory: () => void;
}

export function useToolExecution(): UseToolExecutionReturn {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<unknown | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<ExecutionEntry[]>([]);
  const [elapsedMs, setElapsedMs] = useState(0);

  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const abortRef = useRef(false);
  const startTimeRef = useRef<number>(0);

  const stopTimer = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const invoke = useCallback(
    async (toolName: string, params: Record<string, unknown>) => {
      abortRef.current = false;
      setLoading(true);
      setResult(null);
      setError(null);
      setElapsedMs(0);

      const entryId = `${toolName}-${Date.now()}`;
      const entry: ExecutionEntry = {
        id: entryId,
        toolName,
        params,
        startedAt: new Date(),
        status: "running",
      };

      setHistory((prev) => [entry, ...prev.slice(0, 49)]); // Keep last 50

      // Start elapsed timer
      startTimeRef.current = Date.now();
      timerRef.current = setInterval(() => {
        setElapsedMs(Date.now() - startTimeRef.current);
      }, 100);

      try {
        const toolResult = await engine.invokeTool(toolName, params);

        if (abortRef.current) return;

        const elapsed = Date.now() - startTimeRef.current;
        stopTimer();
        setElapsedMs(elapsed);

        if (toolResult.type === "error") {
          setError(toolResult.output);
          // Panels only receive `result` — without this, their error branches
          // were unreachable and loading spinners stuck on tool failures.
          setResult(toolResult);
          setHistory((prev) =>
            prev.map((e) =>
              e.id === entryId
                ? {
                    ...e,
                    status: "error" as const,
                    error: toolResult.output,
                    completedAt: new Date(),
                    elapsedMs: elapsed,
                  }
                : e
            )
          );
        } else {
          setResult(toolResult);
          setHistory((prev) =>
            prev.map((e) =>
              e.id === entryId
                ? {
                    ...e,
                    status: "success" as const,
                    result: toolResult,
                    completedAt: new Date(),
                    elapsedMs: elapsed,
                  }
                : e
            )
          );
        }
      } catch (err) {
        if (abortRef.current) return;

        const elapsed = Date.now() - startTimeRef.current;
        stopTimer();
        setElapsedMs(elapsed);

        const message = err instanceof Error ? err.message : String(err);
        setError(message);
        setHistory((prev) =>
          prev.map((e) =>
            e.id === entryId
              ? {
                  ...e,
                  status: "error" as const,
                  error: message,
                  completedAt: new Date(),
                  elapsedMs: elapsed,
                }
              : e
          )
        );
      } finally {
        if (!abortRef.current) {
          setLoading(false);
        }
      }
    },
    [stopTimer]
  );

  const abort = useCallback(() => {
    abortRef.current = true;
    stopTimer();
    setLoading(false);
  }, [stopTimer]);

  const reset = useCallback(() => {
    setResult(null);
    setError(null);
    setElapsedMs(0);
  }, []);

  const clearHistory = useCallback(() => {
    setHistory([]);
  }, []);

  // Clear the elapsed timer on unmount to prevent state updates on an
  // unmounted component (e.g. user navigates away during a long tool invocation).
  useEffect(() => {
    return () => {
      stopTimer();
    };
  }, [stopTimer]);

  return useMemo(
    () => ({
      loading,
      result,
      error,
      history,
      elapsedMs,
      invoke,
      abort,
      reset,
      clearHistory,
    }),
    [loading, result, error, history, elapsedMs, invoke, abort, reset, clearHistory],
  );
}
