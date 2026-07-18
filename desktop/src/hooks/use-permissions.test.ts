import { describe, expect, it } from "vitest";

import { pluginBooleanPermissionStatus } from "./use-permissions";

describe("boolean-only AV permission status", () => {
  it("distinguishes a pre-prompt false from an explicit denial", () => {
    expect(pluginBooleanPermissionStatus(false, false)).toBe("not_determined");
    expect(pluginBooleanPermissionStatus(false, true)).toBe("denied");
    expect(pluginBooleanPermissionStatus(true, true)).toBe("granted");
  });
});
