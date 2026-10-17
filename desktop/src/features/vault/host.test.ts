import { afterEach, describe, expect, it, vi } from "vitest";
import type { NativeVaultProviderStatus } from "@/lib/sidecar";

const boundary = vi.hoisted(() => ({
  hostListeners: new Set<(envelope: { session: { user: { id: string } } | null }) => void>(),
  fileImport: vi.fn(async (request: unknown) => ({ echoed: request })),
}));

vi.mock("@/lib/native-vault-auth", () => ({
  subscribeNativeVaultHostEvents: (listener: (envelope: { session: { user: { id: string } } | null }) => void) => {
    boundary.hostListeners.add(listener);
    return () => boundary.hostListeners.delete(listener);
  },
}));
vi.mock("@/lib/native-vault-file-import", () => ({ nativeVaultFileImport: boundary.fileImport }));
vi.mock("@/lib/custodian", () => ({ forgetToken: vi.fn(), getSession: vi.fn(), getToken: vi.fn(), signIn: vi.fn(), subscribeSession: vi.fn() }));
vi.mock("@/lib/app-config", () => ({ getAppRuntimeConfig: () => ({ aidreamServerUrl: "https://server.example" }) }));
vi.mock("@/lib/org/active-org", () => ({
  ACTIVE_ORGANIZATION_CHANGE_EVENT: "test.org-change",
  getActiveOrganizationSnapshot: () => ({ organization: null }),
}));

const events = new EventTarget();
vi.stubGlobal("window", { addEventListener: events.addEventListener.bind(events), removeEventListener: events.removeEventListener.bind(events) });

const { desktopNativeImport, nativeProviderStatusOf } = await import("@/features/vault/host");

const status = (patch: Partial<NativeVaultProviderStatus>): NativeVaultProviderStatus => ({
  supported: true,
  artifact: "built",
  os_enablement: "disabled",
  enrollment: "uninitialized",
  signing_profile: "not_verified",
  ready: false,
  message: "",
  state: "",
  last_configured_subject: null,
  enable_action: "available",
  settings_action: "available",
  ...patch,
});

const emitAccount = (id: string | null) => {
  for (const l of boundary.hostListeners) l({ session: id ? { user: { id } } : null });
};

afterEach(() => boundary.hostListeners.clear());

describe("nativeProviderStatusOf", () => {
  it("is unavailable outside the shell, for an unbuilt provider, or with no way to set it up", () => {
    expect(nativeProviderStatusOf(null)).toBe("unavailable");
    expect(nativeProviderStatusOf(status({ supported: false }))).toBe("unavailable");
    expect(nativeProviderStatusOf(status({ artifact: "not_built" }))).toBe("unavailable");
    expect(nativeProviderStatusOf(status({ enable_action: "unavailable", settings_action: "not_supported" }))).toBe("unavailable");
  });
  it("is ready only when macOS reports AutoFill on, otherwise asks for setup", () => {
    expect(nativeProviderStatusOf(status({ os_enablement: "enabled" }))).toBe("ready");
    expect(nativeProviderStatusOf(status({ os_enablement: "disabled" }))).toBe("needs_setup");
    expect(nativeProviderStatusOf(status({ enable_action: "unavailable" }))).toBe("needs_setup");
  });
});

describe("desktopNativeImport", () => {
  it("sends every request to the Tauri importer unchanged", async () => {
    const request = { action: "status" as const, operation_id: "op-1" };
    await expect(desktopNativeImport.request(request)).resolves.toEqual({ echoed: request });
    expect(boundary.fileImport).toHaveBeenCalledWith(request);
  });

  it("fires on an account change or an organization change, never on the first report or the same account", () => {
    const listener = vi.fn();
    const off = desktopNativeImport.subscribe!(listener);
    emitAccount("user-a"); // the replayed current account
    emitAccount("user-a"); // a token refresh for the same person
    expect(listener).not.toHaveBeenCalled();
    emitAccount("user-b");
    expect(listener).toHaveBeenCalledTimes(1);
    emitAccount(null);
    expect(listener).toHaveBeenCalledTimes(2);
    events.dispatchEvent(new Event("test.org-change"));
    expect(listener).toHaveBeenCalledTimes(3);
    off();
    emitAccount("user-c");
    events.dispatchEvent(new Event("test.org-change"));
    expect(listener).toHaveBeenCalledTimes(3);
  });
});
