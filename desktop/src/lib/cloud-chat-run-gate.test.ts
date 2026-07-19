import { describe, expect, it } from "vitest";
import { CloudChatRunGate } from "./cloud-chat-run-gate";

describe("Cloud Chat run ownership", () => {
  it("rejects a rapid second submit before React can rerender", () => {
    const gate = new CloudChatRunGate();
    expect(gate.tryStart("run-1")).toBe(true);
    expect(gate.tryStart("run-2")).toBe(false);
    expect(gate.finish("run-1")).toBe(true);
    expect(gate.tryStart("run-2")).toBe(true);
  });

  it("prevents a stale finally from releasing a newer run", () => {
    const gate = new CloudChatRunGate();
    expect(gate.tryStart("run-1")).toBe(true);
    gate.cancel();
    expect(gate.tryStart("run-2")).toBe(true);

    expect(gate.finish("run-1")).toBe(false);
    expect(gate.tryStart("run-3")).toBe(false);
    expect(gate.finish("run-2")).toBe(true);
  });
});
