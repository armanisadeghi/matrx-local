import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// Copy/paste goes through @ai-matrx/kit/clipboard (fallback path, never throws, returns a boolean the
// UI branches on). A direct navigator.clipboard call reports "copied" on failure or throws unhandled.
function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.(ts|tsx)$/.test(name) && !/\.test\.(ts|tsx)$/.test(name) ? [path] : [];
  });
}

describe("clipboard access", () => {
  it("no source file calls navigator.clipboard directly", () => {
    const offenders = sources(join(__dirname)).filter((file) =>
      readFileSync(file, "utf8").includes("navigator.clipboard"),
    );
    expect(offenders).toEqual([]);
  });
});
