import { describe, expect, it, vi } from "vitest";
import type { NotesAccessStatus } from "@/lib/api";
import { getFreshNotesAccess } from "./AppActionBanner";

const accessible: NotesAccessStatus = {
  degraded: false,
  reason: null,
  kind: null,
  base_dir: "/Users/test/Documents/Matrx/Notes",
  platform: "darwin",
};

describe("AppActionBanner notes access refresh", () => {
  it("actively re-probes instead of reading the cached denial", async () => {
    const recheckNotesAccess = vi.fn().mockResolvedValue(accessible);

    await expect(getFreshNotesAccess({ recheckNotesAccess })).resolves.toBe(
      accessible,
    );
    expect(recheckNotesAccess).toHaveBeenCalledOnce();
    expect(recheckNotesAccess).toHaveBeenCalledWith(undefined);
  });

  it("uses the same active probe for the create-folder action", async () => {
    const recheckNotesAccess = vi.fn().mockResolvedValue(accessible);

    await getFreshNotesAccess({ recheckNotesAccess }, { createDir: true });

    expect(recheckNotesAccess).toHaveBeenCalledWith({ createDir: true });
  });
});
