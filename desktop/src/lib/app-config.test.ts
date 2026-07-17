import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { from } = vi.hoisted(() => ({ from: vi.fn() }));

vi.mock("@/lib/supabase", () => ({
  default: { from },
}));

class MemoryStorage {
  private values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }

  clear(): void {
    this.values.clear();
  }
}

const storage = new MemoryStorage();

function remoteRow(config: Record<string, unknown>) {
  return {
    data: { config, updated_at: "2026-07-17T12:00:00.000Z" },
    error: null,
  };
}

function mockRemote(result: unknown): void {
  const maybeSingle = vi.fn().mockResolvedValue(result);
  const eq = vi.fn().mockReturnValue({ maybeSingle });
  const select = vi.fn().mockReturnValue({ eq });
  from.mockReturnValue({ select });
}

beforeEach(() => {
  vi.resetModules();
  from.mockReset();
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: storage,
  });
  storage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("desktop runtime app config", () => {
  it("loads a persisted last-good config before the database is reachable", async () => {
    storage.setItem("matrx-app-config-v1", JSON.stringify({
      version: 1,
      aidreamServerUrl: "https://cached.example.com",
      webAppOrigin: "https://cached-web.example.com",
      flags: { safe_mode: true },
      fetchedAt: "2026-07-16T12:00:00.000Z",
    }));
    mockRemote({ data: null, error: new Error("offline") });

    const config = await import("./app-config");

    expect(config.getAppRuntimeConfig()).toMatchObject({
      aidreamServerUrl: "https://cached.example.com",
      webAppOrigin: "https://cached-web.example.com",
      source: "cache",
    });
    await expect(config.refreshAppRuntimeConfig()).resolves.toMatchObject({ source: "cache" });
  });

  it("replaces the cache with a validated remote row without a Vite URL", async () => {
    mockRemote(remoteRow({
      aidream_server_url: "https://remote.example.com/",
      web_app_origin: "https://web.example.com/",
      flags: { maintenance: false },
    }));

    const config = await import("./app-config");
    await expect(config.getAIDreamServerUrl()).resolves.toBe("https://remote.example.com");
    await expect(config.getWebAppOrigin()).resolves.toBe("https://web.example.com");
    expect(JSON.parse(storage.getItem("matrx-app-config-v1") ?? "null")).toMatchObject({
      aidreamServerUrl: "https://remote.example.com",
      webAppOrigin: "https://web.example.com",
    });
  });

  it("rejects malformed remote values and keeps the last good value", async () => {
    storage.setItem("matrx-app-config-v1", JSON.stringify({
      version: 1,
      aidreamServerUrl: "https://cached.example.com",
      webAppOrigin: "https://cached-web.example.com",
      flags: {},
      fetchedAt: "2026-07-16T12:00:00.000Z",
    }));
    mockRemote(remoteRow({
      aidream_server_url: "http://public.example.com",
      flags: {},
    }));

    const config = await import("./app-config");
    await expect(config.getAIDreamServerUrl()).resolves.toBe("https://cached.example.com");
  });
});
