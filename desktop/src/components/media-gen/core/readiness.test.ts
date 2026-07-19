import { describe, expect, it } from "vitest";
import type { ImageGenStatus } from "@/lib/api";
import { needsImageGenPackageInstall } from "./readiness";

function status(
  patch: Partial<ImageGenStatus> = {},
): ImageGenStatus {
  return {
    available: false,
    unavailable_reason: "Optional packages are not installed.",
    loaded_model_id: null,
    is_loading: false,
    load_progress: 0,
    packages_version: null,
    packages_outdated: false,
    device: "cpu",
    ...patch,
  };
}

describe("needsImageGenPackageInstall", () => {
  it("opens the first-time installer when packages are actually missing", () => {
    expect(needsImageGenPackageInstall(status())).toBe(true);
  });

  it("does not block the image surface when installed packages need an update", () => {
    expect(
      needsImageGenPackageInstall(
        status({
          packages_version: "0.37.1",
          packages_outdated: true,
          unavailable_reason: "A required AI runtime update is pending.",
        }),
      ),
    ).toBe(false);
  });

  it("does not install when image generation is ready or still loading", () => {
    expect(needsImageGenPackageInstall(status({ available: true }))).toBe(false);
    expect(needsImageGenPackageInstall(null)).toBe(false);
  });
});
