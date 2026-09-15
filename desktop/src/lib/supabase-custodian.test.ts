import { beforeEach, expect, expectTypeOf, it, vi } from "vitest";
import type supabase from "./supabase";

const mocks = vi.hoisted(() => ({
  listener: null as null | ((s: unknown, rotated: boolean) => void),
  setAuth: vi.fn(async () => undefined),
  createClient: vi.fn(),
}));
vi.mock("@supabase/supabase-js", () => ({
  createClient: (...args: unknown[]) => {
    mocks.createClient(...args);
    return { realtime: { setAuth: mocks.setAuth } };
  },
}));
vi.mock("./custodian", () => ({
  getToken: vi.fn(),
  subscribeSession: (listener: typeof mocks.listener) => { mocks.listener = listener; },
}));
beforeEach(async () => {
  vi.resetModules();
  mocks.setAuth.mockClear();
  mocks.createClient.mockClear();
  await import("./supabase");
});
it("makes disabled session ownership unavailable to TypeScript consumers", () => {
  expectTypeOf<typeof supabase.auth>().toEqualTypeOf<never>();
});
it("reauthorizes realtime on a non-rotation account switch", () => {
  mocks.listener!({ signed_in: true, user_id: "b" }, false);
  expect(mocks.setAuth).toHaveBeenCalledWith();
});
it("reauthorizes realtime on sign-out to discard the previous bearer", () => {
  mocks.listener!({ signed_in: false, user_id: null }, false);
  expect(mocks.setAuth).toHaveBeenCalledWith();
});
it("pins an error upload grant without creating another auth session owner", async () => {
  const { createAccessTokenBoundSupabaseClient } = await import("./supabase");
  const client = createAccessTokenBoundSupabaseClient("pinned-grant");
  expectTypeOf<typeof client.auth>().toEqualTypeOf<never>();
  const options = mocks.createClient.mock.lastCall![2];
  expect(await options.accessToken()).toBe("pinned-grant");
  expect(options.auth).toBeUndefined();
});
