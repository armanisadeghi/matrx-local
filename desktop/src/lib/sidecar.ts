/**
 * Sidecar management for the Python/FastAPI engine.
 *
 * In production (packaged Tauri app), the engine runs as a Tauri sidecar.
 * In development, the engine is expected to be running separately.
 */

import { ENGINE_PORT_BASE, enginePortList } from "@/lib/engine-ports";

type TauriInvoke = <T>(
  cmd: string,
  args?: Record<string, unknown>,
) => Promise<T>;

let invoke: TauriInvoke | null = null;

export class DesktopBridgeUnavailableError extends Error {
  constructor() {
    super(
      "This action requires the Matrx Local desktop app. The desktop command bridge is unavailable in a browser.",
    );
    this.name = "DesktopBridgeUnavailableError";
  }
}

async function loadTauriInvoke() {
  if (invoke) return invoke;
  // Importing @tauri-apps/api succeeds in an ordinary browser too. Its
  // exported invoke() is therefore present and correctly typed, but calling
  // it dereferences window.__TAURI_INTERNALS__.invoke and crashes. Check the
  // host capability before treating the module export as usable.
  if (!isTauri()) return null;
  try {
    const mod = await import("@tauri-apps/api/core");
    invoke = mod.invoke;
    return invoke;
  } catch {
    return null;
  }
}

/** Whether we're running inside a Tauri window. */
export function isTauri(): boolean {
  if (typeof window === "undefined") return false;
  const tauriWindow = window as Window & {
    __TAURI_INTERNALS__?: { invoke?: unknown };
  };
  return typeof tauriWindow.__TAURI_INTERNALS__?.invoke === "function";
}

/**
 * Invoke a required desktop command through the checked runtime boundary.
 *
 * Feature code should use this instead of importing @tauri-apps/api/core
 * directly. The package's TypeScript declaration cannot express whether the
 * current JavaScript host actually injected the Tauri bridge.
 */
export async function invokeTauri<T>(
  cmd: string,
  args?: Record<string, unknown>,
): Promise<T> {
  const inv = await loadTauriInvoke();
  if (!inv) throw new DesktopBridgeUnavailableError();
  return inv<T>(cmd, args);
}

/** Start the Python engine sidecar (Tauri only). */
export async function startSidecar(): Promise<void> {
  const inv = await loadTauriInvoke();
  if (!inv) {
    console.log("[sidecar] Not in Tauri, skipping sidecar start");
    return;
  }
  await inv<void>("start_sidecar");
}

/** Stop the Python engine sidecar (Tauri only). */
export async function stopSidecar(): Promise<void> {
  const inv = await loadTauriInvoke();
  if (!inv) return;
  await inv<void>("stop_sidecar");
}

/** Set whether closing the window hides to tray or quits. */
export async function setCloseToTray(enabled: boolean): Promise<void> {
  const inv = await loadTauriInvoke();
  if (!inv) return;
  await inv<void>("set_close_to_tray", { enabled });
}

export interface UpdateStatus {
  status: "up_to_date" | "available" | "downloading" | "installed";
  version?: string;
  body?: string;
  content_length?: number;
  downloaded?: number;
}

/** Check for updates via the Tauri updater plugin. */
export async function checkForUpdates(install = false): Promise<UpdateStatus> {
  const inv = await loadTauriInvoke();
  if (!inv) return { status: "up_to_date" };
  const result = await inv("check_for_updates", { install }) as UpdateStatus;
  return result;
}

/**
 * Restart the app after an update with a clean shutdown sequence.
 *
 * Calls the Rust `restart_for_update` command which kills the Python sidecar
 * and llama-server before relaunching via the proper Cocoa/WinRT termination
 * handshake. This prevents macOS from generating a crash report on update.
 *
 * Falls back to a direct `relaunch()` if the Rust command is unavailable
 * (e.g. in a browser dev environment).
 */
export async function restartApp(): Promise<void> {
  if (!isTauri()) return;
  const inv = await loadTauriInvoke();
  if (inv) {
    try {
      await inv("restart_for_update");
      return;
    } catch {
      // Rust command not available — fall through to direct relaunch
    }
  }
  try {
    const { relaunch } = await import("@tauri-apps/plugin-process");
    await relaunch();
  } catch {
    // process plugin not available
  }
}

/** Get sidecar process status from Rust (Tauri only). */
export async function getSidecarStatus(): Promise<{ running: boolean; port: number } | null> {
  const inv = await loadTauriInvoke();
  if (!inv) return null;
  try {
    return (await inv("sidecar_status")) as { running: boolean; port: number };
  } catch {
    return null;
  }
}

/**
 * Ask the Rust layer for the app-owned sidecar's port and confirm it is
 * healthy.
 *
 * Rust owns the sidecar process, so this is the AUTHORITATIVE source of the
 * engine URL for this app — unlike ~/.matrx/local.json, which is a shared
 * mutable global that any other engine instance on the machine can overwrite.
 * When the app's own child engine is alive, we must always prefer its port.
 *
 * The health probe goes through the Rust `check_engine_health` command (a
 * native HTTP request) rather than a JS fetch(), so it also works on Windows
 * where WebView2 loopback isolation blocks fetch() to 127.0.0.1.
 *
 * Returns the confirmed base URL (e.g. "http://127.0.0.1:22140") if the owned
 * engine is running and healthy, otherwise null.
 */
export async function getOwnedEngineUrl(): Promise<string | null> {
  const inv = await loadTauriInvoke();
  if (!inv) return null; // dev / browser — no Rust-owned sidecar
  try {
    const status = (await inv("sidecar_status")) as {
      running: boolean;
      port: number;
    } | null;
    if (!status?.running) return null;
    const port = status.port ?? ENGINE_PORT_BASE;
    const healthy = (await inv("check_engine_health", { port })) as boolean;
    if (healthy) return `http://127.0.0.1:${port}`;
  } catch {
    // Rust command unavailable or errored — caller falls back to a port scan.
  }
  return null;
}

/** Get buffered sidecar stdout/stderr lines from Rust (Tauri only). */
export async function getSidecarLogs(): Promise<string[]> {
  const inv = await loadTauriInvoke();
  if (!inv) return [];
  try {
    return (await inv("get_sidecar_logs")) as string[];
  } catch {
    return [];
  }
}

/**
 * Detect if we are running on Windows without depending on PLATFORM data
 * from the engine (which isn't available during startup).
 *
 * Uses navigator.userAgent which is always available in the WebView.
 * This is needed because PLATFORM.is_windows is populated from the engine
 * response — a circular dependency during engine startup.
 */
function isWindowsPlatform(): boolean {
  // navigator.platform is deprecated but still reliable for Win detection.
  // navigator.userAgent is the fallback.
  if (typeof navigator !== "undefined") {
    if (navigator.platform) {
      return navigator.platform.startsWith("Win");
    }
    return navigator.userAgent.includes("Windows");
  }
  return false;
}

/**
 * Wait for the engine health endpoint to respond.
 * Defaults tuned for PyInstaller sidecar cold boot (~10-30s).
 *
 * On Windows inside Tauri, delegates to the Rust `check_engine_health` command
 * because Windows WebView2 loopback network isolation blocks JS fetch() to
 * 127.0.0.1.  On macOS/Linux the original JS fetch() path is preserved.
 *
 * Platform detection uses navigator.userAgent — NOT PLATFORM.is_windows —
 * because PLATFORM is populated from the engine (circular dependency).
 */
export async function waitForEngine(
  baseUrl: string,
  maxRetries = 60,
  intervalMs = 1000
): Promise<boolean> {
  // Use Rust IPC on Windows to bypass WebView2 loopback isolation.
  // Always use Rust IPC in Tauri when available — it's strictly more reliable.
  const useRust = isTauri() && isWindowsPlatform();
  const inv = useRust ? await loadTauriInvoke() : null;

  // Extract port from baseUrl for the Rust path
  const portMatch = baseUrl.match(/:(\d+)/);
  const port = portMatch?.[1] ? parseInt(portMatch[1], 10) : ENGINE_PORT_BASE;

  for (let i = 0; i < maxRetries; i++) {
    try {
      let healthy: boolean;
      if (inv) {
        // Rust HTTP request — not subject to Windows WebView2 loopback isolation
        healthy = (await inv("check_engine_health", { port })) as boolean;
      } else {
        const resp = await fetch(`${baseUrl}/tools/list`, {
          signal: AbortSignal.timeout(2000),
        });
        healthy = resp.ok;
      }
      if (healthy) return true;
    } catch {
      // Not ready yet
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  return false;
}

/**
 * Scan the engine port range and return the first port that responds.
 * This is a standalone helper so the recovery modal can use it independently.
 *
 * On Windows inside Tauri, delegates to the Rust `discover_engine_port` command
 * to bypass WebView2 loopback isolation.  macOS/Linux use JS fetch() unchanged.
 *
 * Uses isWindowsPlatform() (navigator.userAgent) — NOT PLATFORM.is_windows —
 * to avoid the circular dependency on engine data during startup.
 */
export async function discoverEnginePort(): Promise<string | null> {
  if (isTauri() && isWindowsPlatform()) {
    const inv = await loadTauriInvoke();
    if (inv) {
      try {
        const port = (await inv("discover_engine_port")) as number | null;
        if (port != null) return `http://127.0.0.1:${port}`;
      } catch {
        // Fall through to JS scan
      }
    }
  }

  // JS fetch scan — works on macOS/Linux; fallback for Windows if Rust IPC fails
  const ports = enginePortList();
  for (const port of ports) {
    try {
      const resp = await fetch(`http://127.0.0.1:${port}/tools/list`, {
        signal: AbortSignal.timeout(1000),
      });
      if (resp.ok) return `http://127.0.0.1:${port}`;
    } catch {
      continue;
    }
  }
  return null;
}
