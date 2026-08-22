import { describe, expect, it, vi } from "vitest";

import {
  codingSessionActionLabel,
  codingSessionSourceLabel,
  formatRetryDuration,
  runSingleFlight,
} from "./coding-session-ui";

describe("coding session UI helpers", () => {
  it("coalesces overlapping refreshes and permits the next refresh after settlement", async () => {
    let resolve!: (value: number) => void;
    const operation = vi.fn(
      () => new Promise<number>((next) => {
        resolve = next;
      }),
    );
    const flight = { current: null as Promise<number> | null };

    const first = runSingleFlight(flight, operation);
    const overlapping = runSingleFlight(flight, operation);
    expect(overlapping).toBe(first);
    expect(operation).toHaveBeenCalledTimes(1);

    resolve(1);
    await first;
    await Promise.resolve();
    const next = runSingleFlight(flight, () => Promise.resolve(2));
    expect(await next).toBe(2);
  });

  it("formats retry delays without exposing floating-point implementation detail", () => {
    expect(formatRetryDuration(3.621519088745117)).toBe("4s");
    expect(formatRetryDuration(60)).toBe("1m");
    expect(formatRetryDuration(91.2)).toBe("1m 32s");
  });

  it("turns backend action keys into product language", () => {
    expect(codingSessionActionLabel("observe_hook")).toBe("Live event delivery");
    expect(codingSessionActionLabel("custom_action")).toBe("custom action");
    expect(codingSessionSourceLabel("claude_local_jsonl")).toBe("Claude history import");
  });
});
