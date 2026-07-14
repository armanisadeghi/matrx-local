/**
 * Bound an async work queue to at most `limit` concurrent tasks.
 * Extra callers wait their turn — used so the gallery thumb sweep never
 * opens 60 full HTTP streams at once.
 */
export function createConcurrencyLimiter(
  limit: number,
): <T>(task: () => Promise<T>) => Promise<T> {
  if (limit < 1) {
    throw new Error(`concurrency limit must be >= 1, got ${limit}`);
  }
  let active = 0;
  const waiting: Array<() => void> = [];

  const pump = () => {
    while (active < limit && waiting.length > 0) {
      const next = waiting.shift();
      if (next) next();
    }
  };

  return function runLimited<T>(task: () => Promise<T>): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const start = () => {
        active += 1;
        task()
          .then(resolve, reject)
          .finally(() => {
            active -= 1;
            pump();
          });
      };
      if (active < limit) start();
      else waiting.push(start);
    });
  };
}
