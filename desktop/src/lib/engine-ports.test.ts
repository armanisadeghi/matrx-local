import { describe, expect, it } from "vitest";

import { resolveEnginePortBase } from "./engine-ports";

describe("engine world port isolation", () => {
  it("keeps release and development builds in separate fixed ranges", () => {
    expect(resolveEnginePortBase({ dev: false, isolatedSmoke: false })).toBe(22140);
    expect(resolveEnginePortBase({ dev: true, isolatedSmoke: false })).toBe(22240);
  });

  it("keeps a harness build OFF the installed app's band (MXL-D-091)", () => {
    // A `harness`-mode build is a source run served to a browser test: it is a BUILD, so `dev` is
    // false, and before this guard it scanned 22140–22159 and attached to the installed app's
    // engine — the one belonging to the person using this Mac, which no agent may touch.
    expect(resolveEnginePortBase({ dev: false, harness: true, isolatedSmoke: false })).toBe(22240);
    expect(
      resolveEnginePortBase({ dev: false, harness: true, isolatedSmoke: false }),
    ).not.toBe(22140);
    // And the absence of the flag is never the live band by accident for a dev server.
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
