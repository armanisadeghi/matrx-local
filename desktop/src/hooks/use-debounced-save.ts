import { useCallback, useEffect, useMemo, useRef } from "react";

export const DEFAULT_AUTOSAVE_DELAY_MS = 1_200;

export interface DebouncedSaveActions<T> {
  /** Replace the pending value and restart the quiet-period timer. */
  schedule: (value: T) => void;
  /** Persist the pending value now, after any save already in flight. */
  flush: () => Promise<void>;
  /** Discard only the value still waiting for its debounce timer. */
  cancel: () => void;
}

/**
 * Debounce draft persistence without losing the last edit on unmount.
 *
 * Saves are serialized so an older, slower request can never finish after a
 * newer request and overwrite it. The latest pending value is flushed when
 * the editor unmounts; callers should also flush before switching records.
 */
export function useDebouncedSave<T>(
  save: (value: T) => void | Promise<unknown>,
  delayMs = DEFAULT_AUTOSAVE_DELAY_MS,
): DebouncedSaveActions<T> {
  const saveRef = useRef(save);
  saveRef.current = save;

  const timerRef = useRef<number | null>(null);
  const pendingRef = useRef<T | null>(null);
  const hasPendingRef = useRef(false);
  const saveChainRef = useRef<Promise<void>>(Promise.resolve());

  const cancel = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    pendingRef.current = null;
    hasPendingRef.current = false;
  }, []);

  const flush = useCallback((): Promise<void> => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (!hasPendingRef.current) return saveChainRef.current;

    const value = pendingRef.current as T;
    pendingRef.current = null;
    hasPendingRef.current = false;

    saveChainRef.current = saveChainRef.current
      .then(async () => {
        await saveRef.current(value);
      })
      // Feature stores surface their own errors. Keep the queue alive so one
      // failed write never prevents every later edit from being persisted.
      .catch(() => undefined);
    return saveChainRef.current;
  }, []);

  const schedule = useCallback(
    (value: T) => {
      pendingRef.current = value;
      hasPendingRef.current = true;
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
      timerRef.current = window.setTimeout(() => {
        timerRef.current = null;
        void flush();
      }, delayMs);
    },
    [delayMs, flush],
  );

  useEffect(
    () => () => {
      void flush();
    },
    [flush],
  );

  return useMemo(
    () => ({ schedule, flush, cancel }),
    [schedule, flush, cancel],
  );
}
