import { describe, expect, it } from "vitest";

import {
  actionNeededFromAccessResource,
  actionNeededFromDownload,
  actionNeededFromLiveDownload,
  actionNeededFromPermission,
} from "./adapters";
import type { AccessHealth, AccessResourceHealth } from "@/lib/api";

describe("permission action adapter", () => {
  it.each(["denied", "not_determined", "restricted"])(
    "publishes an explicit action for %s",
    (status) => {
      const item = actionNeededFromPermission({
        permission: "screen_recording",
        status,
        feature: "Screen recording",
        source: "devices-permission:screen_recording",
        observedAt: 123,
      });
      expect(item?.fingerprint).toBe(
        "os-permission:screen_recording:Screen recording",
      );
      expect(item?.action).toMatchObject({
        kind: "request_os_permission",
        permission_key: "screen_recording",
        route: "/devices?permission=screen_recording",
      });
    },
  );

  it.each(["granted", "loading", "unknown", "unavailable"])(
    "does not diagnose %s as denied",
    (status) => {
      expect(
        actionNeededFromPermission({
          permission: "camera",
          status,
          feature: "Camera",
          source: "test",
        }),
      ).toBeNull();
    },
  );
});

describe("download action-needed scope", () => {
  const restoredFailure = {
    id: "old-hf-failure",
    category: "image_gen",
    filename: "gated-model",
    display_name: "Gated model",
    urls: ["https://huggingface.co/org/model/resolve/main/model.bin"],
    total_bytes: 0,
    bytes_done: 0,
    percent: 0,
    status: "failed" as const,
    error_msg: "license gate",
    resolution: {
      code: "hf_gate_not_accepted",
      title: "Accept this model's terms on Hugging Face",
      message: "Accept the model terms, then retry.",
      action_kind: "open_url" as const,
      action_label: "Open model page",
      action_url: "https://huggingface.co/org/model",
      provider: "huggingface",
    },
    priority: 0,
    part_current: 0,
    part_total: 1,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    completed_at: null,
    snapshot: true,
  };

  it("keeps restored failures in Downloads without promoting them globally", () => {
    expect(actionNeededFromDownload(restoredFailure)).not.toBeNull();
    expect(actionNeededFromLiveDownload(restoredFailure)).toBeNull();
  });

  it("promotes a failure produced by current-session download work", () => {
    expect(
      actionNeededFromLiveDownload({ ...restoredFailure, snapshot: false }),
    ).not.toBeNull();
  });
});

describe("access action-needed scope", () => {
  const notesResource: AccessResourceHealth = {
    resource_id: "notes-canonical",
    label: "Notes folder",
    root: "/Users/test/Documents/Matrx/Notes",
    provenance: "default",
    status: "degraded",
    kind: "permission",
    message: "Full Disk Access is not granted.",
    capabilities: {},
    last_success_at: null,
    last_failure: null,
    recent: [],
    generation: 1,
  };
  const health: AccessHealth = {
    generation: 1,
    platform: "darwin",
    degraded: true,
    resources: [notesResource],
    fda: {
      status: "denied",
      evidence: [],
      source: "engine-process probe",
      checked_at: 1,
    },
  };

  it("keeps a helper-only denial on Documents instead of the global banner", () => {
    expect(actionNeededFromAccessResource(notesResource, health, true)).toBeNull();
  });

  it("promotes a corroborated parent-and-helper denial globally", () => {
    expect(
      actionNeededFromAccessResource(notesResource, health, false),
    ).not.toBeNull();
  });
});
