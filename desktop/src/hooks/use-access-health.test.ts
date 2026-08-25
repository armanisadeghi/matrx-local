/**
 * deriveAccessPresentation — the copy contract that kills the false-FDA bug.
 *
 * The definitive "Full Disk Access" CLAIM may render ONLY when the engine
 * helper and Tauri parent app both establish the denial. Windows/Linux never
 * see the words at all. Mapped-dir failures name their own folder and never
 * claim FDA. (Backend wording is pinned separately in
 * tests/smoke/test_access_health.py — this pins the frontend layer.)
 */

import { describe, expect, it } from "vitest";
import { deriveAccessPresentation } from "./use-access-health";
import type { AccessResourceHealth } from "@/lib/api";

function resource(
  overrides: Partial<AccessResourceHealth> = {},
): AccessResourceHealth {
  return {
    resource_id: "notes-canonical",
    label: "Notes folder",
    root: "/Users/test/Documents/Matrx/Notes",
    provenance: "default",
    status: "degraded",
    kind: "permission",
    message: "The OS denied write on /Users/test/Documents/Matrx/Notes (errno 13).",
    capabilities: {},
    last_success_at: null,
    last_failure: null,
    recent: [],
    generation: 1,
    ...overrides,
  };
}

import type { AccessFdaDiagnosis } from "@/lib/api";

const fdaDenied: AccessFdaDiagnosis = {
  status: "denied",
  evidence: [],
  source: "engine-process probe",
  checked_at: 0,
};
const fdaGranted: AccessFdaDiagnosis = { ...fdaDenied, status: "granted" };
const fdaUnknown: AccessFdaDiagnosis = { ...fdaDenied, status: "indeterminate" };

describe("deriveAccessPresentation", () => {
  it("claims FDA only when the engine helper and parent app agree", () => {
    const p = deriveAccessPresentation(
      resource(),
      { platform: "darwin", fda: fdaDenied },
      false,
    );
    expect(p.title).toContain("Full Disk Access");
    expect(p.primaryAction).toBe("open_settings");
    expect(p.showFdaAction).toBe(true);
  });

  it("does not turn a helper-only denial into a false FDA claim", () => {
    const p = deriveAccessPresentation(
      resource({
        capabilities: {
          enumerate: {
            path: "/Users/test/Documents/Matrx/Notes",
            capability: "enumerate",
            ok: false,
            errno: 1,
            error: "Operation not permitted",
            op: "listing notes",
            source: "probe",
            at: 1,
            generation: 1,
          },
        },
        last_failure: {
          path: "/Users/test/Documents/Matrx/Notes",
          capability: "enumerate",
          ok: false,
          errno: 1,
          error: "Operation not permitted",
          op: "listing notes",
          source: "probe",
          at: 1,
          generation: 1,
        },
      }),
      { platform: "darwin", fda: fdaDenied },
      true,
    );
    expect(p.title).not.toContain("Full Disk Access");
    expect(p.body).toContain("does not prove");
    expect(p.primaryAction).toBe("check_again");
    expect(p.showFdaAction).toBe(false);
    expect(p.scope).toBe("contextual");
  });

  it("does not claim FDA when the parent-app result is unavailable", () => {
    const p = deriveAccessPresentation(
      resource(),
      { platform: "darwin", fda: fdaDenied },
      null,
    );
    expect(p.title).not.toContain("Full Disk Access");
    expect(p.body).toContain("does not prove");
    expect(p.showFdaAction).toBe(false);
    expect(p.scope).toBe("contextual");
  });

  it("does NOT claim FDA when the diagnosis is indeterminate", () => {
    const p = deriveAccessPresentation(
      resource(),
      { platform: "darwin", fda: fdaUnknown },
      null,
    );
    expect(p.title).not.toContain("Full Disk Access");
    expect(p.body).not.toContain("are blocking");
    expect(p.primaryAction).toBe("check_again");
    // Settings shortcut still offered as a secondary option on macOS.
    expect(p.showFdaAction).toBe(true);
  });

  it("hedges (no claim) when only the parent-app probe says not granted", () => {
    const p = deriveAccessPresentation(
      resource(),
      { platform: "darwin", fda: fdaUnknown },
      false,
    );
    expect(p.body).toContain("appears not to be granted");
    expect(p.body).not.toContain("Privacy controls are blocking");
  });

  it("exonerated FDA (granted) → evidence message, no FDA claim", () => {
    const p = deriveAccessPresentation(
      resource(),
      { platform: "darwin", fda: fdaGranted },
      true,
    );
    expect(p.title).not.toContain("Full Disk Access");
    expect(p.primaryAction).toBe("check_again");
  });

  it.each(["win32", "linux"] as const)(
    "%s never mentions Full Disk Access",
    (platform) => {
      const p = deriveAccessPresentation(
        resource(),
        { platform, fda: null },
        null,
      );
      expect(p.title + p.body).not.toContain("Full Disk Access");
      expect(p.showFdaAction).toBe(false);
    },
  );

  it("mapped-dir failure names its own folder and never claims FDA — even when FDA is denied", () => {
    const p = deriveAccessPresentation(
      resource({
        resource_id: "notes-mapping:f1:abcd1234",
        label: "Mapped folder: /Volumes/Archive/Notes",
        root: "/Volumes/Archive/Notes",
        provenance: "mapped",
        message: "The OS denied replace on /Volumes/Archive/Notes (errno 13).",
      }),
      { platform: "darwin", fda: fdaDenied },
      null,
    );
    expect(p.title).toContain("mapped folder");
    expect(p.body).toContain("/Volumes/Archive/Notes");
    expect(p.title + p.body).not.toContain("Full Disk Access");
    expect(p.showFdaAction).toBe(false);
  });

  it("missing dir → create-folder action, no FDA", () => {
    const p = deriveAccessPresentation(
      resource({ kind: "missing_dir" }),
      { platform: "darwin", fda: fdaUnknown },
      null,
    );
    expect(p.primaryAction).toBe("create_folder");
    expect(p.showFdaAction).toBe(false);
    expect(p.body).toContain("missing at");
  });
});
