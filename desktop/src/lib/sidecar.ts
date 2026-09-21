/**
 * Sidecar management for the Python/FastAPI engine.
 *
 * In production (packaged Tauri app), the engine runs as a Tauri sidecar.
 * In development, the engine is expected to be running separately.
 */

import { ENGINE_PORT_BASE, enginePortList } from "@/lib/engine-ports";
import type { EngineSupervisorStatus } from "@/lib/engine-supervisor";

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

/** Host-measured status for the native Vault provider shell. It deliberately
 * has no token, credential identity, or OS-enablement claim. */
export interface NativeVaultProviderStatus {
  supported: boolean;
  artifact: "built" | "not_built" | "not_supported";
  os_enablement: "enabled" | "disabled" | "unavailable" | "not_supported";
  enrollment: "uninitialized" | "configured" | "invalidated" | "state_corrupt" | "busy" | "state_unavailable" | "unsupported_platform";
  signing_profile: "not_verified" | "not_supported";
  ready: false;
  message: string;
  state: string;
  last_configured_subject: string | null;
  enable_action: "available" | "not_supported" | "unavailable";
  settings_action: "available" | "not_supported" | "unavailable";
}

export interface NativeVaultProviderAction {
  outcome: "enabled" | "disabled" | "opened" | "failed" | "pending" | "cooldown" | "unavailable";
  message: string;
}

export async function getNativeVaultProviderStatus(): Promise<NativeVaultProviderStatus | null> {
  const inv = await loadTauriInvoke();
  if (!inv) return null;
  return inv<NativeVaultProviderStatus>("native_vault_provider_status");
}

export async function requestNativeVaultProviderEnable(): Promise<NativeVaultProviderAction | null> {
  const inv = await loadTauriInvoke();
  if (!inv) return null;
  return inv<NativeVaultProviderAction>("request_native_vault_provider_enable");
}

export async function openNativeVaultProviderSettings(): Promise<NativeVaultProviderAction | null> {
  const inv = await loadTauriInvoke();
  if (!inv) return null;
  return inv<NativeVaultProviderAction>("open_native_vault_provider_settings");
}

export type NativeVaultTransition = "applied" | "unchanged" | "state_unavailable" | "state_corrupt" | "busy" | "unsupported_platform";

export async function invalidateNativeVaultHostActor(): Promise<NativeVaultTransition | null> {
  // Browser/dev has no native Vault container by design. This is the explicit
  // accepted platform outcome, distinct from a broken bridge in Tauri.
  if (!isTauri()) return "unsupported_platform";
  const inv = await loadTauriInvoke();
  if (!inv) return null;
  try {
    return await inv<NativeVaultTransition>("invalidate_native_vault_host_actor");
  } catch {
    return null;
  }
}

export async function reconcileNativeVaultHostActor(subject: string | null): Promise<NativeVaultTransition | null> {
  // See invalidateNativeVaultHostActor: only an identified non-Tauri runtime
  // receives this value. A Tauri bridge failure remains a fence refusal.
  if (!isTauri()) return "unsupported_platform";
  const inv = await loadTauriInvoke();
  if (!inv) return null;
  try {
    return await inv<NativeVaultTransition>("reconcile_native_vault_host_actor", { subject });
  } catch {
    return null;
  }
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

/** Restart the owned engine through the canonical PID-scoped Rust path. */
export async function restartSidecar(): Promise<void> {
  await invokeTauri<void>("restart_sidecar");
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
  /** BYTES — the HTTP Content-Length of the update artifact, from the Tauri
   * updater's download event. Unlike most `*_length` fields in this fleet it is
   * not a character count. */
  content_length?: number;
  downloaded?: number;
}

/**
 * The version of the app bundle **as it currently sits on disk**.
 *
 * Distinct from the compile-time `APP_VERSION` the renderer carries: an
 * auto-update replaces the bundle under a running process, so this is the build
 * that would launch next, not the one executing. `null` means genuinely
 * unreadable (dev binary, non-macOS installer) — the UI renders that as
 * unknown, never as "same as running". See `lib/version-facts.ts`.
 */
export async function getInstalledAppVersion(): Promise<string | null> {
  const inv = await loadTauriInvoke();
  if (!inv) return null;
  try {
    return (await inv<string | null>("installed_app_version")) ?? null;
  } catch {
    return null;
  }
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
 * Calls the Rust `restart_app` command which shuts down the Python sidecar
 * and llama-server before relaunching via the proper Cocoa/WinRT termination
 * handshake. This prevents macOS from generating a crash report on update.
 *
 * Throws if the ownership-safe Rust command is unavailable. A direct process
 * relaunch can strand engine-owned children and is intentionally not used.
 */
export async function restartApp(reason = "user requested application restart"): Promise<void> {
  await invokeTauri<void>("restart_app", { reason });
}

/** Reload only the renderer while leaving the engine and native services running. */
export async function reloadRenderer(): Promise<void> {
  await invokeTauri<void>("reload_renderer");
}

export interface SidecarStatus {
  running: boolean;
  port: number;
  supervisor: EngineSupervisorStatus;
}

/** Get the native owner's process and recovery status (Tauri only). */
export async function getSidecarStatus(): Promise<SidecarStatus | null> {
  const inv = await loadTauriInvoke();
  if (!inv) return null;
  try {
    return (await inv("sidecar_status")) as SidecarStatus;
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
    const status = (await inv("sidecar_status")) as SidecarStatus | null;
    if (!status?.running) return null;
    const port = status.port ?? ENGINE_PORT_BASE;
    const healthy = (await inv("check_engine_health", { port })) as boolean;
    if (healthy) return `http://127.0.0.1:${port}`;
  } catch {
    // Rust command unavailable or errored — caller falls back to a port scan.
  }
  return null;
}

export const ENGINE_STARTUP_TIMEOUT_SECONDS = 300;

export type OwnedEngineStartupResult =
  | { outcome: "ready"; url: string }
  | { outcome: "exited" | "timed_out" | "unavailable"; url: null };

type OwnedEngineStartupProbe = () => Promise<
  | { outcome: "ready"; url: string }
  | { outcome: "running" | "recovering" | "exited"; url: null }
>;

/**
 * Poll an owned process until it is ready, exits, or reaches its deadline.
 * Exported separately so the timing and liveness contract can be tested
 * without a Tauri runtime.
 */
export async function waitForOwnedEngineProbe(
  probe: OwnedEngineStartupProbe,
  maxRetries: number,
  intervalMs: number,
): Promise<OwnedEngineStartupResult> {
  for (let attempt = 0; attempt < maxRetries; attempt += 1) {
    const result = await probe();
    if (result.outcome === "ready") return result;
    if (result.outcome === "exited") return { outcome: "exited", url: null };
    if (attempt + 1 < maxRetries) {
      await new Promise((resolve) => setTimeout(resolve, intervalMs));
    }
  }
  return { outcome: "timed_out", url: null };
}

/**
 * Wait for this app's Rust-owned engine child to become reachable.
 *
 * Cold packaged starts can legitimately take more than a minute while large
 * Python modules initialize. Read the child status and current owned port on
 * every poll so a live, progressing process is not mislabeled as crashed and
 * an automatically selected alternate port is followed immediately.
 */
export async function waitForOwnedEngine(
  maxRetries = ENGINE_STARTUP_TIMEOUT_SECONDS,
  intervalMs = 1000,
): Promise<OwnedEngineStartupResult> {
  const inv = await loadTauriInvoke();
  if (!inv) return { outcome: "unavailable", url: null };

  return waitForOwnedEngineProbe(async () => {
    try {
      const status = (await inv("sidecar_status")) as SidecarStatus;
      if (!status.running) {
        return {
          outcome: status.supervisor.phase === "failed" ? "exited" as const : "recovering" as const,
          url: null,
        };
      }

      const healthy = (await inv("check_engine_health", {
        port: status.port,
      })) as boolean;
      if (healthy) {
        return {
          outcome: "ready" as const,
          url: `http://127.0.0.1:${status.port}`,
        };
      }
    } catch {
      // A transient IPC/probe failure does not prove that the child exited.
    }
    return { outcome: "running" as const, url: null };
  }, maxRetries, intervalMs);
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
        const resp = await fetch(`${baseUrl}/health`, {
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
      const resp = await fetch(`http://127.0.0.1:${port}/health`, {
        signal: AbortSignal.timeout(1000),
      });
      if (resp.ok) return `http://127.0.0.1:${port}`;
    } catch {
      continue;
    }
  }
  return null;
}
