/**
 * The custody cutover's handover, and the split-brain rule that goes with it (CS-19).
 *
 * Proven failing before the fix: with no handover at all, the first test's daemon stays
 * `signed_out` and `adopt` is never called — which is exactly what Arman's Mac did on 1.4.124
 * (live, 2026-09-15: the daemon reported `{"state":"signed_out","world":"live"}` the morning after
 * an upgrade that had been signed in the day before).
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  findLegacySession,
  handOverLegacySession,
  resetLegacyHandoverForTests,
  type HandoverDeps,
  type HandoverResult,
} from "@/lib/legacy-session-handover";
import type { SessionSnapshot } from "@/lib/custodian";

const NEVER_SIGNED_IN: SessionSnapshot = {
  signed_in: false,
  user_id: null,
  email: null,
  state: "signed_out",
  state_reason: "Sign in on this computer to start syncing your folders.",
  since: null,
  next_attempt_at: null,
  cloud_state_write_pending: false,
};

const SIGNED_IN: SessionSnapshot = {
  ...NEVER_SIGNED_IN,
  signed_in: true,
  user_id: "4cf62e4e",
  email: "admin@admin.com",
  state: "signed_in",
  state_reason: "Signed in and syncing.",
};

/** A `localStorage` holding exactly what supabase-js persisted before the cutover. */
function storageWith(entries: Record<string, string>) {
  const map = new Map(Object.entries(entries));
  return {
    get length() {
      return map.size;
    },
    key: (index: number) => [...map.keys()][index] ?? null,
    getItem: (key: string) => map.get(key) ?? null,
    removeItem: (key: string) => void map.delete(key),
    has: (key: string) => map.has(key),
  };
}

function deps(
  storage: ReturnType<typeof storageWith>,
  result: HandoverResult | Error,
): HandoverDeps & { adopt: ReturnType<typeof vi.fn> } {
  const adopt = vi.fn(async () => {
    if (result instanceof Error) throw result;
    return result;
  });
  return { storage, adopt, log: () => {} } as never;
}

beforeEach(() => resetLegacyHandoverForTests());

describe("finding the pre-cutover session", () => {
  it("reads the plain JSON entry supabase-js wrote", () => {
    const found = findLegacySession(
      storageWith({
        "sb-db-auth-token": JSON.stringify({
          refresh_token: "pre-cutover",
          user: { email: "admin@admin.com" },
        }),
      }),
    );
    expect(found).toEqual({
      key: "sb-db-auth-token",
      refreshToken: "pre-cutover",
      email: "admin@admin.com",
    });
  });

  it("reads the base64 encoding supabase-js also uses", () => {
    const payload = JSON.stringify({ refresh_token: "r2", user: { email: "a@b.c" } });
    const found = findLegacySession(
      storageWith({ "sb-anything-auth-token": `base64-${btoa(payload)}` }),
    );
    expect(found?.refreshToken).toBe("r2");
    expect(found?.email).toBe("a@b.c");
  });

  it("ignores unrelated keys so a fresh install finds nothing", () => {
    expect(findLegacySession(storageWith({ theme: "dark", "matrx.lastPage": "/chat" }))).toBeNull();
  });

  it("still reports an entry it cannot decode, because it proves this Mac had a session", () => {
    const found = findLegacySession(storageWith({ "sb-db-auth-token": "{not json" }));
    expect(found).toEqual({ key: "sb-db-auth-token", refreshToken: null, email: null });
  });
});

describe("the handover", () => {
  it("offers the pre-cutover session and reports the state the daemon reached", async () => {
    const storage = storageWith({
      "sb-db-auth-token": JSON.stringify({
        refresh_token: "pre-cutover",
        user: { email: "admin@admin.com" },
      }),
    });
    const d = deps(storage, { outcome: "adopted", spent: true });
    const read = vi
      .fn<() => Promise<SessionSnapshot>>()
      .mockResolvedValueOnce(SIGNED_IN);

    const snapshot = await handOverLegacySession(NEVER_SIGNED_IN, read, d);

    expect(d.adopt).toHaveBeenCalledWith("pre-cutover", "admin@admin.com");
    expect(snapshot?.signed_in).toBe(true);
    // A refresh token nobody reads is still a refresh token: the entry is gone.
    expect(storage.has("sb-db-auth-token")).toBe(false);
  });

  it("never offers anything on a device the daemon already has a session for", async () => {
    const storage = storageWith({
      "sb-db-auth-token": JSON.stringify({ refresh_token: "stale" }),
    });
    const d = deps(storage, { outcome: "adopted", spent: true });
    await handOverLegacySession(SIGNED_IN, async () => SIGNED_IN, d);
    expect(d.adopt).not.toHaveBeenCalled();
  });

  it("never re-offers after the daemon has decided a sign-in is needed", async () => {
    const storage = storageWith({
      "sb-db-auth-token": JSON.stringify({ refresh_token: "rotated-away" }),
    });
    const d = deps(storage, { outcome: "adopted", spent: true });
    const decided: SessionSnapshot = {
      ...NEVER_SIGNED_IN,
      state: "sign_in_needed",
      state_reason:
        "Sign in again to AI Matrx — this update changed how this computer keeps you signed in.",
    };
    await handOverLegacySession(decided, async () => decided, d);
    expect(d.adopt).not.toHaveBeenCalled();
  });

  it("keeps its one chance when the daemon cannot be reached", async () => {
    const storage = storageWith({
      "sb-db-auth-token": JSON.stringify({ refresh_token: "still-good" }),
    });
    const d = deps(storage, new Error("AI Matrx Sync is not running on this computer."));
    const snapshot = await handOverLegacySession(NEVER_SIGNED_IN, async () => NEVER_SIGNED_IN, d);
    // Nothing was done, so nothing is republished — and the credential is still there.
    expect(snapshot).toBeNull();
    expect(storage.has("sb-db-auth-token")).toBe(true);
  });

  it("keeps its one chance when the offer was not spent", async () => {
    const storage = storageWith({
      "sb-db-auth-token": JSON.stringify({ refresh_token: "still-good" }),
    });
    const d = deps(storage, { outcome: "retry", spent: false });
    expect(
      await handOverLegacySession(NEVER_SIGNED_IN, async () => NEVER_SIGNED_IN, d),
    ).toBeNull();
    expect(storage.has("sb-db-auth-token")).toBe(true);
  });

  it("runs at most once per process", async () => {
    const storage = storageWith({
      "sb-db-auth-token": JSON.stringify({ refresh_token: "pre-cutover" }),
    });
    const d = deps(storage, { outcome: "adopted", spent: true });
    await handOverLegacySession(NEVER_SIGNED_IN, async () => NEVER_SIGNED_IN, d);
    await handOverLegacySession(NEVER_SIGNED_IN, async () => NEVER_SIGNED_IN, d);
    expect(d.adopt).toHaveBeenCalledTimes(1);
  });
});
