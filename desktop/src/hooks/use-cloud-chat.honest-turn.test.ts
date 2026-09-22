/**
 * A screen is absent or honest — never a failed turn that is still running.
 *
 * On 2026-09-22 the Cloud Chat surface told the owner his turn had failed and
 * that same turn went on delegating tool calls to his Mac for nine more
 * minutes. Every exit from the stream loop that ends a turn on screen must
 * first ask whether the SERVER turn is still alive
 * (`followIfTurnIsAlive` → `isTurnStillRunning`), and must not claim failure,
 * stopping or completion when it is.
 *
 * This is a source guard rather than a render test because the exits are
 * spread across a 900-line send closure: the thing that must not regress is
 * that a NEW exit cannot be added without asking.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const SOURCE = readFileSync(
  fileURLToPath(new URL("./use-cloud-chat.ts", import.meta.url)),
  "utf8",
);
const LINES = SOURCE.split("\n");

/** Statuses that tell the person this turn is over. */
const TERMINAL_STATUSES = [
  '"Request failed."',
  '"Stopped."',
  '"Stream failed."',
  '"Stream failed after partial response."',
];

/** Every `updateAssistant({ … })` call in the file, with its line number. */
function updateAssistantBlocks(): Array<{ line: number; body: string }> {
  const blocks: Array<{ line: number; body: string }> = [];
  LINES.forEach((line, index) => {
    if (!line.includes("updateAssistant({")) return;
    blocks.push({ line: index + 1, body: LINES.slice(index, index + 14).join("\n") });
  });
  return blocks;
}

function askedAbove(lineNumber: number, within = 32): boolean {
  return LINES.slice(Math.max(0, lineNumber - 1 - within), lineNumber - 1).some((line) =>
    line.includes("followIfTurnIsAlive"),
  );
}

describe("the Cloud Chat surface never reports a turn that is still running", () => {
  it("asks whether the turn is alive before ending the reply with an error", () => {
    const unguarded = updateAssistantBlocks()
      .filter((block) => /^\s*error: /m.test(block.body))
      .filter((block) => !askedAbove(block.line))
      .map((block) => `line ${block.line}`);
    expect(
      unguarded,
      "an assistant reply was ended with an error without asking whether the server turn is still running",
    ).toEqual([]);
  });

  it("asks before writing a terminal status onto the reply", () => {
    const unguarded = updateAssistantBlocks()
      .filter((block) =>
        TERMINAL_STATUSES.some((status) => block.body.includes(`streamStatus: ${status}`)),
      )
      .filter((block) => !askedAbove(block.line))
      .map((block) => `line ${block.line}`);
    expect(unguarded, "a turn was declared over without asking if it is").toEqual([]);
  });

  it("holds the composer while a detached turn is still running", () => {
    expect(SOURCE).toContain("liveTurnRef.current");
    expect(SOURCE).toContain("This turn is still running");
  });

  it("keeps re-reading the conversation until the turn is quiet more than once", () => {
    expect(SOURCE).toContain("LIVE_TURN_QUIET_CHECKS");
    expect(SOURCE).toMatch(/quiet\s*>=\s*LIVE_TURN_QUIET_CHECKS/);
  });
});
