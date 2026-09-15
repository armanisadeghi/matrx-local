import { beforeEach, expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({ listener: null as null | ((s: unknown, rotated: boolean) => void), setAuth: vi.fn(async () => undefined) }));
vi.mock("@supabase/supabase-js", () => ({ createClient: () => ({ realtime: { setAuth: mocks.setAuth } }) }));
vi.mock("./custodian", () => ({ getToken: vi.fn(), subscribeSession: (listener: typeof mocks.listener) => { mocks.listener = listener; } }));
beforeEach(async () => { vi.resetModules(); mocks.setAuth.mockClear(); await import("./supabase"); });
it("reauthorizes realtime on a non-rotation account switch", () => { mocks.listener!({ signed_in: true, user_id: "b" }, false); expect(mocks.setAuth).toHaveBeenCalledWith(); });
it("reauthorizes realtime on sign-out to discard the previous bearer", () => { mocks.listener!({ signed_in: false, user_id: null }, false); expect(mocks.setAuth).toHaveBeenCalledWith(); });
