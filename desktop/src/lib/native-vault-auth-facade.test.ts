import { afterEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ callback: undefined as undefined | ((event: string, session: { user: { id: string } } | null) => void) }));
vi.mock("@/lib/supabase", () => ({ default: { auth: { onAuthStateChange: vi.fn((callback) => { mocks.callback = callback; return { data: { subscription: { unsubscribe: vi.fn() } } }; }) } } }));
vi.mock("@/lib/sidecar", () => ({ invalidateNativeVaultHostActor: async () => "unsupported_platform", reconcileNativeVaultHostActor: async () => "unsupported_platform" }));
vi.mock("@/lib/api", () => ({ engine: { prepareSessionTransition: async () => ({ status: "unavailable" }) } }));

afterEach(() => { vi.useRealTimers(); vi.resetModules(); mocks.callback = undefined; });

it("accepts anonymous lifecycle state and isolates throwing subscribers", async () => {
  vi.useFakeTimers();
  const auth = await import("./native-vault-auth");
  const received = vi.fn();
  auth.subscribeNativeVaultHostEvents(() => { throw new Error("listener failure"); });
  auth.subscribeNativeVaultHostEvents(received);
  mocks.callback?.("SIGNED_OUT", null);
  expect(received).toHaveBeenCalledOnce();
  await vi.runAllTimersAsync();
  const envelope = received.mock.calls[0]?.[0];
  expect(envelope).toBeDefined();
  await expect(envelope!.completion).resolves.toEqual({ accepted: true });
  expect(auth.isNativeVaultHostSessionAdopted(null)).toBe(true);
  expect(auth.isNativeVaultHostSessionAdopted(undefined)).toBe(false);
  expect(auth.nativeVaultAdoptedHostGeneration(null)).toBe(envelope!.revision);
  expect(auth.nativeVaultAdoptedHostGeneration(undefined)).toBeNull();
});

it("replaces late replay with the newer lifecycle envelope", async () => {
  const auth = await import("./native-vault-auth");
  auth.subscribeNativeVaultHostEvents(() => undefined);
  mocks.callback?.("INITIAL_SESSION", { user: { id: "66666666-6666-4666-8666-666666666666" } });
  const received = vi.fn();
  auth.subscribeNativeVaultHostEvents(received);
  mocks.callback?.("SIGNED_OUT", null);
  await Promise.resolve();
  expect(received).toHaveBeenCalledOnce();
  expect(received.mock.calls[0]?.[0].event).toBe("SIGNED_OUT");
});
