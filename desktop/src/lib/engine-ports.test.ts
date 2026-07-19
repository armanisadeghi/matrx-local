import { describe, expect, it } from "vitest";

import { resolveEnginePortBase } from "./engine-ports";

describe("engine world port isolation", () => {
  it("keeps release and development builds in separate fixed ranges", () => {
    expect(resolveEnginePortBase({ dev: false, isolatedSmoke: false })).toBe(22140);
    expect(resolveEnginePortBase({ dev: true, isolatedSmoke: false })).toBe(22240);
  });

  it("uses the run-specific smoke range even for a production build", () => {
    expect(
      resolveEnginePortBase({
        dev: false,
        isolatedSmoke: true,
        smokePortBase: "23740",
      }),
    ).toBe(23740);
  });

  it("rejects live, dev, missing, and malformed smoke port bases", () => {
    for (const smokePortBase of [undefined, "22140", "22240", "nope", "65530"]) {
      expect(() =>
        resolveEnginePortBase({
          dev: false,
          isolatedSmoke: true,
          smokePortBase,
        }),
      ).toThrow("Isolated smoke build requires");
    }
  });
});
