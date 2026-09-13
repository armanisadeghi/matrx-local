import { describe, expect, it } from "vitest";

import {
  ALL_PERMISSION_KEYS,
  authorityFor,
  hookAuthoritativeKeys,
  isCountable,
  isGranted,
  pluginBooleanPermissionStatus,
  summarizePermissions,
  PERMISSION_META,
  type PermissionKey,
  type PermissionState,
  type PermissionStatus,
} from "./use-permissions";

function stateOf(key: PermissionKey, status: PermissionStatus): PermissionState {
  const meta = PERMISSION_META[key];
  return {
    key,
    status,
    label: meta.label,
    description: meta.description,
    tools: meta.tools,
    authority: authorityFor(key, "mac") ?? "engine",
    canPrompt: false,
    settingsUrl: meta.settingsUrl,
  };
}

function mapOf(entries: Array<[PermissionKey, PermissionStatus]>): Map<PermissionKey, PermissionState> {
  return new Map(entries.map(([key, status]) => [key, stateOf(key, status)]));
}

describe("boolean-only AV permission status", () => {
  it("distinguishes a pre-prompt false from an explicit denial", () => {
    expect(pluginBooleanPermissionStatus(false, false)).toBe("not_determined");
    expect(pluginBooleanPermissionStatus(false, true)).toBe("denied");
    expect(pluginBooleanPermissionStatus(true, true)).toBe("granted");
  });
});

describe("the one permission count (Dashboard 6/18 → 10/19 regression)", () => {
  it("reports no number until every key has answered", () => {
    const summary = summarizePermissions(
      mapOf([
        ["microphone", "granted"],
        ["camera", "granted"],
        ["contacts", "loading"],
      ]),
    );
    expect(summary.complete).toBe(false);
    // Partial numbers are exactly what changed under the user's eyes before.
    expect(summary.granted).toBe(2);
    expect(summary.total).toBe(2);
  });

  it("never counts first-use, unavailable or unreadable keys in the denominator", () => {
    const summary = summarizePermissions(
      mapOf([
        ["microphone", "granted"],
        ["contacts", "not_determined"],
        ["automation", "first_use"],
        ["local_network", "first_use"],
        ["input_monitoring", "unavailable"],
        ["screen_recording", "unknown"],
      ]),
    );
    expect(summary.complete).toBe(true);
    expect(summary.total).toBe(2);
    expect(summary.granted).toBe(1);
    expect(summary.firstUse).toEqual(["automation", "local_network"]);
    expect(summary.unknown).toEqual(["screen_recording"]);
  });

  it("treats a scoped grant (limited) as granted", () => {
    const summary = summarizePermissions(
      mapOf([
        ["photos", "limited"],
        ["calendar", "limited"],
        ["reminders", "denied"],
      ]),
    );
    expect(summary.granted).toBe(2);
    expect(summary.total).toBe(3);
    expect(isGranted("limited")).toBe(true);
    expect(isGranted("not_determined")).toBe(false);
  });

  it("only counts statuses macOS can actually report", () => {
    expect(isCountable("granted")).toBe(true);
    expect(isCountable("denied")).toBe(true);
    expect(isCountable("not_determined")).toBe(true);
    expect(isCountable("restricted")).toBe(true);
    expect(isCountable("first_use")).toBe(false);
    expect(isCountable("unknown")).toBe(false);
    expect(isCountable("unavailable")).toBe(false);
    expect(isCountable("loading")).toBe(false);
  });
});

describe("who owns each permission (the process that can actually prompt)", () => {
  it("routes the keys the engine could never prompt for to the app process on macOS", () => {
    for (const key of ["contacts", "calendar", "reminders", "photos", "location", "speech_recognition", "bluetooth"] as const) {
      expect(authorityFor(key, "mac")).toBe("app");
    }
  });

  it("keeps screen recording with the engine on macOS (its process does the capture)", () => {
    expect(authorityFor("screen_recording", "mac")).toBe("engine");
  });

  it("marks Automation and Local Network as first-use only — nothing to switch on beforehand", () => {
    expect(authorityFor("automation", "mac")).toBe("first_use");
    expect(authorityFor("local_network", "mac")).toBe("first_use");
  });

  it("does not invent macOS-only permissions on Windows or Linux", () => {
    for (const platform of ["windows", "linux"] as const) {
      expect(authorityFor("full_disk_access", platform)).toBeNull();
      expect(authorityFor("contacts", platform)).toBeNull();
      expect(authorityFor("accessibility", platform)).toBeNull();
      expect(authorityFor("microphone", platform)).toBe("engine");
      expect(authorityFor("screen_recording", platform)).toBe("engine");
    }
  });

  it("names every key the hook (not the engine list) is authoritative for on macOS", () => {
    const keys = hookAuthoritativeKeys("mac");
    expect(keys.has("screen_recording")).toBe(false);
    for (const key of ALL_PERMISSION_KEYS) {
      if (key === "screen_recording") continue;
      expect(keys.has(key)).toBe(true);
    }
  });

  it("has no row for things that are not OS permissions (Wi‑Fi scans, Mail, Messages, 'network')", () => {
    const keys = new Set<string>(ALL_PERMISSION_KEYS);
    for (const notAPermission of ["wifi", "network", "mail", "messages"]) {
      expect(keys.has(notAPermission)).toBe(false);
    }
  });
});
