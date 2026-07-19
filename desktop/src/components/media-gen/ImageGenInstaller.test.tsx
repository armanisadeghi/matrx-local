import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { MediaRuntimeStatus } from "@/lib/api";

const ensure = vi.fn();
const repair = vi.fn();
const refresh = vi.fn();
let runtime: MediaRuntimeStatus | null = null;

vi.mock("@/contexts/MediaGenContext", () => ({
  useMediaGenApp: () => [
    {
      mediaRuntime: runtime,
      mediaRuntimeLoading: false,
      mediaRuntimeError: null,
    },
    {
      ensureMediaRuntime: ensure,
      repairMediaRuntime: repair,
      refreshMediaRuntime: refresh,
    },
  ],
}));

import { ImageGenInstaller } from "./ImageGenInstaller";

function makeRuntime(
  patch: Partial<MediaRuntimeStatus>,
): MediaRuntimeStatus {
  return {
    state: "absent",
    operation: null,
    attempt_id: null,
    runtime_revision: null,
    required_revision: "runtime-v1",
    stage: "",
    percent: 0,
    message: "",
    failure_code: null,
    failure_detail: null,
    repairable: false,
    image_available: false,
    video_packages_available: false,
    ...patch,
  };
}

describe("ImageGenInstaller runtime panel", () => {
  beforeEach(() => {
    runtime = null;
    vi.clearAllMocks();
  });

  it("unmounts immediately when the authoritative runtime is ready", () => {
    runtime = makeRuntime({
      state: "ready",
      image_available: true,
      video_packages_available: true,
    });
    expect(renderToStaticMarkup(<ImageGenInstaller />)).toBe("");
  });

  it("renders an explicit repair action for a repairable failure", () => {
    runtime = makeRuntime({
      state: "failed",
      repairable: true,
      failure_code: "activation_failed",
      failure_detail: "Transformers could not activate in the frozen engine.",
    });
    const html = renderToStaticMarkup(<ImageGenInstaller />);
    expect(html).toContain("AI runtime needs repair");
    expect(html).toContain("Repair and fully validate");
    expect(html).toContain("activation_failed");
    expect(html).not.toContain("Packages Ready");
  });

  it("renders bounded progress for the current operation, never a success banner", () => {
    runtime = makeRuntime({
      state: "validating",
      operation: "repair",
      attempt_id: "attempt-2",
      stage: "frozen-imports",
      percent: 84,
      message: "Validating every managed dependency…",
    });
    const html = renderToStaticMarkup(<ImageGenInstaller />);
    expect(html).toContain("Repairing AI runtime");
    expect(html).toContain("frozen-imports");
    expect(html).toContain("84%");
    expect(html).not.toContain("AI Packages Ready");
  });
});
