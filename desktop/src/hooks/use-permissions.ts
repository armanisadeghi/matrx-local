/**
 * usePermissions — the ONE model of OS privacy permissions for the desktop.
 *
 * Every permission key names exactly one AUTHORITY — the process that can
 * both read its status truthfully and make macOS show its prompt:
 *
 *   plugin   tauri-plugin-macos-permissions in THIS app process
 *            (microphone, camera, accessibility, full disk access, input
 *            monitoring). The plugin reads the framework status; the prompt
 *            (mic/camera) or the System Settings hand-off (the rest) is the
 *            app's own.
 *   app      Rust commands in THIS app process (`src-tauri/src/tcc.rs`):
 *            contacts, calendar, reminders, photos, location, speech
 *            recognition, bluetooth. macOS attributes the prompt to the app
 *            bundle, which carries every NS*UsageDescription key and has the
 *            run loop the frameworks need. The nested Python engine has
 *            neither — its requests were silently ignored, which is why
 *            "Request access" used to do nothing and the app never appeared
 *            in System Settings → Location Services.
 *   engine   The Python engine: screen recording. Screen capture runs in the
 *            engine process and its CGRequestScreenCaptureAccess is what
 *            lists the app under Screen Recording. On Windows/Linux every
 *            platform permission is engine-reported.
 *   first_use  Not queryable by any public API (Automation / Apple Events,
 *            Local Network). macOS asks the first time the app uses them;
 *            there is nothing to switch on before that. These are shown as
 *            a STATE and never counted.
 *
 * The count on the Dashboard comes from ONE place — `summary` — computed
 * over queryable keys only, and only reported once every key has answered.
 * Before this, the Dashboard mixed two lists with different lengths (an
 * engine list that included Wi‑Fi scans and Mail, a plugin map that did not)
 * and the number changed under the user's eyes ("6/18", then "10/19").
 *
 * Known Apple quirks handled here:
 * - CGPreflightScreenCaptureAccess() reads false in the app process until
 *   relaunch after an in-session grant; the ENGINE's answer is used.
 * - Camera/Microphone plugin checks are booleans; a false before this UI
 *   ever asked is "not determined", not "denied".
 * - SCShareableContent must never be used for status (it re-triggers the
 *   Sequoia 30-day consent prompt) — see checker.py.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { isTauri } from "@/lib/sidecar";
import { PLATFORM } from "@/lib/platformCtx";
import { engine, type PermissionInfo } from "@/lib/api";
import { enqueueDurableClientError } from "@/lib/error-outbox";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type PermissionKey =
  | "microphone"
  | "camera"
  | "screen_recording"
  | "accessibility"
  | "full_disk_access"
  | "input_monitoring"
  | "contacts"
  | "calendar"
  | "reminders"
  | "photos"
  | "bluetooth"
  | "location"
  | "speech_recognition"
  | "local_network"
  | "automation";

export type PermissionStatus =
  | "granted"
  /** Granted with a scope the person chose (limited Contacts/Photos, write-only Calendar). */
  | "limited"
  | "denied"
  | "not_determined"
  | "restricted"
  /** macOS decides on first use; not queryable, never counted. */
  | "first_use"
  | "unavailable"
  | "unknown"
  | "loading";

export type PermissionAuthority = "plugin" | "app" | "engine" | "first_use";

export interface PermissionState {
  key: PermissionKey;
  status: PermissionStatus;
  label: string;
  description: string;
  tools: string[];
  /** Who answers and who prompts for this key on THIS platform. */
  authority: PermissionAuthority;
  /** true = a click makes macOS show its own dialog; false = System Settings */
  canPrompt: boolean;
  settingsUrl: string;
  detail?: string;
}

export interface PermissionSummary {
  /** granted + limited, over queryable keys on this platform */
  granted: number;
  /** queryable keys on this platform (never includes first-use or unavailable) */
  total: number;
  /** false while any key is still loading — do not show numbers before this */
  complete: boolean;
  /** keys macOS approves on first use — shown as a state, never counted */
  firstUse: PermissionKey[];
  /** keys whose status could not be read (engine offline, framework missing) */
  unknown: PermissionKey[];
}

// ---------------------------------------------------------------------------
// Static metadata
// ---------------------------------------------------------------------------

type Platform = "mac" | "windows" | "linux";

interface PermissionMeta {
  label: string;
  description: string;
  tools: string[];
  /** Authority on macOS; on other platforms every listed key is engine-owned. */
  macAuthority: PermissionAuthority;
  /** Platforms where this permission exists at all. */
  platforms: Platform[];
  settingsUrl: string;
}

const SETTINGS = "x-apple.systempreferences:com.apple.preference.security";

export const PERMISSION_META: Record<PermissionKey, PermissionMeta> = {
  microphone: {
    label: "Microphone",
    description: "Audio recording, live transcription, voice tools",
    tools: ["RecordAudio", "TranscribeAudio", "ListAudioDevices", "PlayAudio"],
    macAuthority: "plugin",
    platforms: ["mac", "windows", "linux"],
    settingsUrl: `${SETTINGS}?Privacy_Microphone`,
  },
  camera: {
    label: "Camera",
    description: "Camera capture for vision and document tools",
    tools: ["CaptureCamera"],
    macAuthority: "plugin",
    platforms: ["mac", "windows", "linux"],
    settingsUrl: `${SETTINGS}?Privacy_Camera`,
  },
  screen_recording: {
    label: "Screen Recording",
    description: "Screenshot tool and screen-based automation",
    tools: ["Screenshot", "BrowserScreenshot"],
    macAuthority: "engine",
    platforms: ["mac", "windows", "linux"],
    settingsUrl: `${SETTINGS}?Privacy_ScreenCapture`,
  },
  accessibility: {
    label: "Accessibility",
    description: "Keyboard simulation, mouse control, window management",
    tools: ["TypeText", "Hotkey", "MouseClick", "MouseMove", "ListWindows", "FocusWindow", "MoveWindow", "MinimizeWindow", "FocusApp"],
    macAuthority: "plugin",
    platforms: ["mac"],
    settingsUrl: `${SETTINGS}?Privacy_Accessibility`,
  },
  full_disk_access: {
    label: "Full Disk Access",
    description: "Read and write files outside standard app folders; Messages history",
    tools: ["ReadFile", "WriteFile", "ListDirectory", "SearchFiles", "DeleteFile", "ListMessages"],
    macAuthority: "plugin",
    platforms: ["mac"],
    settingsUrl: `${SETTINGS}?Privacy_AllFiles`,
  },
  input_monitoring: {
    label: "Input Monitoring",
    description: "Global keyboard and mouse event monitoring",
    tools: ["MonitorInput"],
    macAuthority: "plugin",
    platforms: ["mac"],
    settingsUrl: `${SETTINGS}?Privacy_ListenEvent`,
  },
  contacts: {
    label: "Contacts",
    description: "Read and search your address book",
    tools: ["SearchContacts", "GetContact"],
    macAuthority: "app",
    platforms: ["mac"],
    settingsUrl: `${SETTINGS}?Privacy_Contacts`,
  },
  calendar: {
    label: "Calendar",
    description: "Read and create calendar events",
    tools: ["ListEvents", "CreateEvent"],
    macAuthority: "app",
    platforms: ["mac"],
    settingsUrl: `${SETTINGS}?Privacy_Calendars`,
  },
  reminders: {
    label: "Reminders",
    description: "Read and create reminders in macOS Reminders",
    tools: ["ListReminders", "CreateReminder"],
    macAuthority: "app",
    platforms: ["mac"],
    settingsUrl: `${SETTINGS}?Privacy_Reminders`,
  },
  photos: {
    label: "Photos Library",
    description: "Read images from your photo library",
    tools: ["SearchPhotos", "GetPhoto"],
    macAuthority: "app",
    platforms: ["mac"],
    settingsUrl: `${SETTINGS}?Privacy_Photos`,
  },
  bluetooth: {
    label: "Bluetooth",
    description: "Discover and list nearby Bluetooth devices",
    tools: ["BluetoothDevices", "ConnectedDevices"],
    macAuthority: "app",
    platforms: ["mac", "windows", "linux"],
    settingsUrl: `${SETTINGS}?Privacy_Bluetooth`,
  },
  location: {
    label: "Location Services",
    description: "Access current GPS/network location",
    tools: ["GetLocation"],
    macAuthority: "app",
    platforms: ["mac", "windows"],
    settingsUrl: `${SETTINGS}?Privacy_LocationServices`,
  },
  speech_recognition: {
    label: "Speech Recognition",
    description: "Transcribe audio using Apple's on-device speech engine",
    tools: ["TranscribeWithAppleSpeech", "ListSpeechLocales"],
    macAuthority: "app",
    platforms: ["mac"],
    settingsUrl: `${SETTINGS}?Privacy_SpeechRecognition`,
  },
  local_network: {
    label: "Local Network",
    description: "Discover devices and services on your local network",
    tools: ["NetworkScan", "MDNSDiscover", "WifiNetworks"],
    macAuthority: "first_use",
    platforms: ["mac"],
    settingsUrl: `${SETTINGS}?Privacy_LocalNetwork`,
  },
  automation: {
    label: "Automation (Apple Events)",
    description: "Send commands to other apps via AppleScript — including Mail",
    tools: ["AppleScript", "LaunchApp", "FocusApp", "SendEmail", "ListEmails"],
    macAuthority: "first_use",
    platforms: ["mac"],
    settingsUrl: `${SETTINGS}?Privacy_Automation`,
  },
};

export const ALL_PERMISSION_KEYS = Object.keys(PERMISSION_META) as PermissionKey[];

function currentPlatform(): Platform {
  if (PLATFORM.is_mac) return "mac";
  if (PLATFORM.is_windows) return "windows";
  return "linux";
}

/** Authority for `key` on the running platform (`null` = does not exist here). */
export function authorityFor(key: PermissionKey, platform: Platform = currentPlatform()): PermissionAuthority | null {
  const meta = PERMISSION_META[key];
  if (!meta.platforms.includes(platform)) return null;
  return platform === "mac" ? meta.macAuthority : "engine";
}

/** Keys the Tauri plugin answers for (macOS). Exported for consumers that
 * merge engine device rows with this hook's statuses. */
export const PLUGIN_KEYS = new Set<PermissionKey>(
  ALL_PERMISSION_KEYS.filter((key) => PERMISSION_META[key].macAuthority === "plugin"),
);

/** Keys the app's own Rust commands answer for (macOS). */
export const APP_KEYS = new Set<PermissionKey>(
  ALL_PERMISSION_KEYS.filter((key) => PERMISSION_META[key].macAuthority === "app"),
);

/** Keys whose status on this platform comes from THIS hook, not the engine
 * device list. Pages that also render engine rows must override those rows'
 * status with the hook's value for these keys, or they will contradict the
 * Dashboard. */
export function hookAuthoritativeKeys(platform: Platform = currentPlatform()): Set<PermissionKey> {
  return new Set(
    ALL_PERMISSION_KEYS.filter((key) => {
      const authority = authorityFor(key, platform);
      return authority === "plugin" || authority === "app" || authority === "first_use";
    }),
  );
}

// How long to wait after firing a prompt request before re-checking status.
// AVFoundation completionHandler fires async; we need a small buffer.
const POST_REQUEST_DELAY_MS = 1200;
const AV_REQUESTED_PREFIX = "matrx:permission-requested:";

function wasExplicitlyRequested(key: "microphone" | "camera"): boolean {
  try {
    return localStorage.getItem(`${AV_REQUESTED_PREFIX}${key}`) === "1";
  } catch {
    return false;
  }
}

function markExplicitlyRequested(key: "microphone" | "camera"): void {
  try {
    localStorage.setItem(`${AV_REQUESTED_PREFIX}${key}`, "1");
  } catch {
    // A denied storage write must not prevent the OS grant request.
  }
}

export function pluginBooleanPermissionStatus(
  granted: boolean,
  explicitlyRequested: boolean,
): PermissionStatus {
  if (granted) return "granted";
  return explicitlyRequested ? "denied" : "not_determined";
}

/**
 * Accessibility, Full Disk Access, and Input Monitoring have no public
 * authorization-status enum. Their plugin probes can only prove a grant;
 * a false result does not tell us whether the person has never enabled it,
 * declined it, or is constrained by device policy. Keep that uncertainty
 * honest rather than presenting it as "Not asked yet".
 */
export function settingsOnlyBooleanPermissionStatus(granted: boolean): PermissionStatus {
  return granted ? "granted" : "unknown";
}

const PROBE_CAUSAL_SIGNATURE = {
  pluginMicrophone: "permission-probe:plugin:microphone",
  pluginCamera: "permission-probe:plugin:camera",
  pluginAccessibility: "permission-probe:plugin:accessibility",
  pluginFullDiskAccess: "permission-probe:plugin:full-disk-access",
  pluginInputMonitoring: "permission-probe:plugin:input-monitoring",
  pluginScreenRecording: "permission-probe:plugin:screen-recording",
  app: "permission-probe:app",
} as const;

function reportPermissionProbeFailure(
  source: string,
  causalSignature: string,
  error: unknown,
): void {
  console.error(`[permissions] ${source} failed:`, error);
  enqueueDurableClientError({
    level: "error",
    source,
    message: `Permission status probe failed in ${source}.`,
    causalSignature,
    requireIdentity: true,
  });
}

function pluginProbeCausalSignature(key: PermissionKey): string {
  switch (key) {
    case "microphone": return PROBE_CAUSAL_SIGNATURE.pluginMicrophone;
    case "camera": return PROBE_CAUSAL_SIGNATURE.pluginCamera;
    case "accessibility": return PROBE_CAUSAL_SIGNATURE.pluginAccessibility;
    case "full_disk_access": return PROBE_CAUSAL_SIGNATURE.pluginFullDiskAccess;
    case "input_monitoring": return PROBE_CAUSAL_SIGNATURE.pluginInputMonitoring;
    default: return PROBE_CAUSAL_SIGNATURE.app;
  }
}

/**
 * The canonical, read-only plugin status read. Microphone and camera retain
 * their explicit-request marker because the plugin exposes only a boolean.
 * Settings-only permissions intentionally report false as unknown.
 */
export async function readPluginPermissionStatus(key: PermissionKey): Promise<PermissionStatus> {
  if (!isTauri()) return "unknown";
  const perms = await import("tauri-plugin-macos-permissions-api");
  switch (key) {
    case "microphone":
      return pluginBooleanPermissionStatus(
        await perms.checkMicrophonePermission(),
        wasExplicitlyRequested("microphone"),
      );
    case "camera":
      return pluginBooleanPermissionStatus(
        await perms.checkCameraPermission(),
        wasExplicitlyRequested("camera"),
      );
    case "accessibility":
      return settingsOnlyBooleanPermissionStatus(await perms.checkAccessibilityPermission());
    case "full_disk_access":
      return settingsOnlyBooleanPermissionStatus(await perms.checkFullDiskAccessPermission());
    case "input_monitoring":
      return settingsOnlyBooleanPermissionStatus(await perms.checkInputMonitoringPermission());
    default:
      return "unknown";
  }
}

/** Prompt for microphone access from the same app process that will capture
 * audio, then return the post-prompt state. The explicit-request marker is
 * written before opening the OS dialog so a declined prompt is distinguishable
 * from first use on the next read. */
export async function requestPluginMicrophonePermission(): Promise<PermissionStatus> {
  if (!isTauri()) return "unknown";
  const perms = await import("tauri-plugin-macos-permissions-api");
  markExplicitlyRequested("microphone");
  await perms.requestMicrophonePermission();
  await delay(POST_REQUEST_DELAY_MS);
  return readPluginPermissionStatus("microphone");
}

export function transcriptionMicrophonePrerequisiteError(status: PermissionStatus): string | null {
  if (status === "granted") return null;
  if (status === "restricted") {
    return "Microphone access is restricted on this device (parental controls or MDM policy).";
  }
  if (status === "denied") {
    return "Microphone access was denied. Open System Settings → Privacy & Security → Microphone and enable access for Matrx Local.";
  }
  return "Microphone access is required for transcription. Please allow access when prompted.";
}

/** Is this status a usable grant? `limited` is a grant the person scoped. */
export function isGranted(status: PermissionStatus): boolean {
  return status === "granted" || status === "limited";
}

/** Does this status count toward the "N of M" number? */
export function isCountable(status: PermissionStatus): boolean {
  return status !== "unavailable" && status !== "first_use" && status !== "unknown" && status !== "loading";
}

/**
 * The ONE summary every surface shows. Pure so it can be tested and so the
 * Dashboard, the Permissions modal and the Setup wizard can never disagree.
 */
export function summarizePermissions(
  permissions: Map<PermissionKey, PermissionState>,
): PermissionSummary {
  let granted = 0;
  let total = 0;
  let complete = true;
  const firstUse: PermissionKey[] = [];
  const unknown: PermissionKey[] = [];
  for (const state of permissions.values()) {
    if (state.status === "loading") {
      complete = false;
      continue;
    }
    if (state.status === "first_use") {
      firstUse.push(state.key);
      continue;
    }
    if (state.status === "unknown") {
      unknown.push(state.key);
      continue;
    }
    if (!isCountable(state.status)) continue;
    total += 1;
    if (isGranted(state.status)) granted += 1;
  }
  return { granted, total, complete, firstUse, unknown };
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export interface UsePermissionsReturn {
  permissions: Map<PermissionKey, PermissionState>;
  isLoading: boolean;
  /** The one count. `complete === false` means "still checking" — show that, not a number. */
  summary: PermissionSummary;
  /**
   * The engine's raw per-device rows (Devices page: device inventories,
   * instructions). ONE shared copy. For keys in `hookAuthoritativeKeys()`
   * the STATUS in these rows is not authoritative — this hook's Map is.
   */
  devicePermissions: PermissionInfo[];
  devicePlatform: string;
  deviceLastRefresh: Date | null;
  refreshDevicePermissions: (force?: boolean) => Promise<void>;
  check: (key: PermissionKey) => Promise<PermissionStatus>;
  checkAll: () => Promise<void>;
  request: (key: PermissionKey) => Promise<void>;
  openSettings: (key: PermissionKey) => Promise<void>;
}

interface TccPermissionState {
  permission: string;
  status: string;
  detail: string | null;
  owner: string;
}

const TCC_STATUSES = new Set<PermissionStatus>([
  "granted",
  "limited",
  "denied",
  "not_determined",
  "restricted",
  "unavailable",
]);

function tccStatus(raw: string): PermissionStatus {
  return TCC_STATUSES.has(raw as PermissionStatus) ? (raw as PermissionStatus) : "unknown";
}

function engineStatus(raw: string): PermissionStatus {
  switch (raw) {
    case "granted":
    case "denied":
    case "not_determined":
    case "restricted":
    case "unavailable":
      return raw;
    default:
      return "unknown";
  }
}

function buildInitialState(): Map<PermissionKey, PermissionState> {
  const map = new Map<PermissionKey, PermissionState>();
  const platform = currentPlatform();
  for (const key of ALL_PERMISSION_KEYS) {
    const meta = PERMISSION_META[key];
    const authority = authorityFor(key, platform);
    map.set(key, {
      key,
      status: authority === null ? "unavailable" : "loading",
      label: meta.label,
      description: meta.description,
      tools: meta.tools,
      authority: authority ?? "engine",
      canPrompt:
        authority === "app" ||
        authority === "engine" ||
        (authority === "plugin" && (key === "microphone" || key === "camera")),
      settingsUrl: meta.settingsUrl,
    });
  }
  return map;
}

const delay = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export function usePermissions(): UsePermissionsReturn {
  const [permissions, setPermissions] = useState<Map<PermissionKey, PermissionState>>(
    buildInitialState,
  );
  const [isLoading, setIsLoading] = useState(true);
  const [devicePermissions, setDevicePermissions] = useState<PermissionInfo[]>([]);
  const [devicePlatform, setDevicePlatform] = useState("");
  const [deviceLastRefresh, setDeviceLastRefresh] = useState<Date | null>(null);

  // ── Helpers ────────────────────────────────────────────────────────────────

  const updatePermission = useCallback(
    (key: PermissionKey, status: PermissionStatus, detail?: string) => {
      setPermissions((prev) => {
        const current = prev.get(key);
        if (!current) return prev; // unknown key: never invent a row
        const next = new Map(prev);
        const { detail: _stale, ...rest } = current;
        next.set(key, { ...rest, status, ...(detail ? { detail } : {}) });
        return next;
      });
    },
    [],
  );

  /**
   * Plugin-native check (this app process). Read-only, never prompts:
   *   microphone / camera  → AVCaptureDevice.authorizationStatus
   *   accessibility        → AXIsProcessTrusted
   *   full_disk_access     → file-system probe
   *   input_monitoring     → IOHIDCheckAccess
   */
  const checkPluginPermission = useCallback(
    async (key: PermissionKey): Promise<PermissionStatus> => {
      try {
        return await readPluginPermissionStatus(key);
      } catch (error) {
        reportPermissionProbeFailure(
          `permission-probe:plugin:${key}`,
          pluginProbeCausalSignature(key),
          error,
        );
        return "unknown";
      }
    },
    [],
  );

  /** App-owned check (Rust, this process). Read-only class-level status. */
  const checkAppPermission = useCallback(
    async (key: PermissionKey): Promise<{ status: PermissionStatus; detail?: string }> => {
      if (!isTauri()) return { status: "unknown", detail: "Available in the desktop app only." };
      try {
        const { invoke } = await import("@tauri-apps/api/core");
        const result = await invoke<TccPermissionState>("tcc_permission_status", { permission: key });
        const status = tccStatus(result.status);
        return result.detail ? { status, detail: result.detail } : { status };
      } catch (err) {
        console.error(`[permissions] app status for ${key} failed:`, err);
        reportPermissionProbeFailure("permission-probe:app", PROBE_CAUSAL_SIGNATURE.app, err);
        return { status: "unknown", detail: "The app could not read this permission's status." };
      }
    },
    [],
  );

  /** Engine-owned check (screen recording on macOS; everything on Windows/Linux). */
  const checkEnginePermission = useCallback(
    async (key: PermissionKey): Promise<{ status: PermissionStatus; detail?: string }> => {
      try {
        const result = await engine.getDevicePermission(key);
        return { status: engineStatus(result.status), detail: result.user_details || result.details };
      } catch {
        if (key === "screen_recording" && isTauri()) {
          // Engine not up yet. The app's own preflight can only prove a grant
          // (true); false here is NOT a denial — report unknown, not a guess.
          try {
            const perms = await import("tauri-plugin-macos-permissions-api");
            if (await perms.checkScreenRecordingPermission()) return { status: "granted" };
          } catch (fallbackError) {
            reportPermissionProbeFailure(
              "permission-probe:plugin:screen-recording",
              PROBE_CAUSAL_SIGNATURE.pluginScreenRecording,
              fallbackError,
            );
            // fall through
          }
        }
        return { status: "unknown", detail: "Confirmed once the engine is running." };
      }
    },
    [],
  );

  // ── Check ──────────────────────────────────────────────────────────────────

  const check = useCallback(
    async (key: PermissionKey): Promise<PermissionStatus> => {
      const authority = authorityFor(key);
      if (authority === null) {
        updatePermission(key, "unavailable");
        return "unavailable";
      }
      if (authority === "first_use") {
        updatePermission(
          key,
          "first_use",
          "macOS asks for this the first time the app uses it. Until then there is nothing to switch on.",
        );
        return "first_use";
      }
      if (authority === "plugin") {
        const status = await checkPluginPermission(key);
        updatePermission(key, status);
        return status;
      }
      if (authority === "app") {
        const { status, detail } = await checkAppPermission(key);
        updatePermission(key, status, detail);
        return status;
      }
      const { status, detail } = await checkEnginePermission(key);
      updatePermission(key, status, detail);
      return status;
    },
    [checkAppPermission, checkEnginePermission, checkPluginPermission, updatePermission],
  );

  const checkAll = useCallback(async () => {
    setIsLoading(true);
    await Promise.all(ALL_PERMISSION_KEYS.map((key) => check(key)));
    setIsLoading(false);
  }, [check]);

  /**
   * Refresh the shared engine device rows (Devices page). `force` bypasses
   * the engine's TTL cache — use from explicit "Refresh" affordances only.
   * Engine-authority keys fold their status back into the Map so the moment
   * the engine comes up, screen recording stops reading "unknown".
   */
  const refreshDevicePermissions = useCallback(
    async (force: boolean = false) => {
      try {
        const result = await engine.getDevicePermissions(force);
        setDevicePermissions(result.permissions);
        setDevicePlatform(result.platform);
        setDeviceLastRefresh(new Date());
        for (const p of result.permissions) {
          const key = p.permission as PermissionKey;
          if (!(key in PERMISSION_META)) continue;
          if (authorityFor(key) !== "engine") continue;
          updatePermission(key, engineStatus(p.status), p.user_details || p.details);
        }
      } catch {
        // Engine unreachable — keep the last known list.
      }
    },
    [updatePermission],
  );

  // ── Request ────────────────────────────────────────────────────────────────

  const openSettings = useCallback(async (key: PermissionKey) => {
    const meta = PERMISSION_META[key];
    if (!meta.settingsUrl) return;
    if (isTauri()) {
      const { open } = await import("@tauri-apps/plugin-shell");
      await open(meta.settingsUrl);
    } else {
      window.open(meta.settingsUrl, "_blank");
    }
  }, []);

  const request = useCallback(
    async (key: PermissionKey) => {
      const authority = authorityFor(key);
      if (authority === null) return;
      if (!isTauri()) {
        await openSettings(key);
        return;
      }

      // ── App-owned (Rust): the real prompt, then the real answer ──────────
      if (authority === "app") {
        try {
          const { invoke } = await import("@tauri-apps/api/core");
          const result = await invoke<TccPermissionState>("tcc_request_permission", { permission: key });
          const status = tccStatus(result.status);
          updatePermission(key, status, result.detail ?? undefined);
          // Denied/restricted: the answer is recorded and the app IS listed in
          // System Settings now — that pane is the only place to change it.
          // Not-determined: the prompt is still open (or Location Services are
          // off, which the detail says) — sending the user to Settings would
          // show them a list without the app in it.
          if (status === "denied" || status === "restricted") await openSettings(key);
        } catch (err) {
          console.error(`[permissions] app request for ${key} failed:`, err);
          await check(key);
        }
        return;
      }

      // ── Engine-owned ──────────────────────────────────────────────────────
      if (authority === "engine") {
        if (key === "screen_recording" && PLATFORM.is_mac) {
          // Screen capture runs in the engine, so the ENGINE asks — and until
          // it calls CGRequestScreenCaptureAccess once, macOS never lists it
          // under Screen Recording. On Sequoia a grant only takes effect on
          // the next launch, so System Settings still opens afterwards.
          try {
            await engine.post("/devices/permissions/request/screen-recording", {});
          } catch (err) {
            console.error("[permissions] engine screen-recording request failed:", err);
          }
          await delay(POST_REQUEST_DELAY_MS);
          const status = await check(key);
          if (status !== "granted") await openSettings(key);
          return;
        }
        try {
          await engine.post(`/devices/permissions/request/${key}`, {});
          await delay(POST_REQUEST_DELAY_MS);
          const status = await check(key);
          if (status !== "granted") await openSettings(key);
        } catch (err) {
          console.error(`[permissions] engine request for ${key} failed:`, err);
          await openSettings(key);
        }
        return;
      }

      // ── First-use: nothing to ask; the Settings pane is the only control ──
      if (authority === "first_use") {
        await openSettings(key);
        return;
      }

      // ── Plugin-owned ──────────────────────────────────────────────────────
      switch (key) {
        case "microphone":
        case "camera": {
          try {
            if (key === "microphone") {
              updatePermission(key, await requestPluginMicrophonePermission());
            } else {
              const perms = await import("tauri-plugin-macos-permissions-api");
              markExplicitlyRequested(key);
              await perms.requestCameraPermission();
              await delay(POST_REQUEST_DELAY_MS);
              await check(key);
            }
          } catch (err) {
            console.error(`[permissions] Failed to request ${key}:`, err);
            await openSettings(key);
          }
          break;
        }
        case "accessibility":
        case "full_disk_access":
        case "input_monitoring": {
          // No in-app dialog exists for these; the plugin's request opens the
          // exact System Settings pane (same as openSettings).
          try {
            const perms = await import("tauri-plugin-macos-permissions-api");
            if (key === "accessibility") await perms.requestAccessibilityPermission();
            else if (key === "full_disk_access") await perms.requestFullDiskAccessPermission();
            else await perms.requestInputMonitoringPermission();
          } catch (err) {
            console.error(`[permissions] Failed to open settings for ${key}:`, err);
            await openSettings(key);
          }
          break;
        }
        default:
          await openSettings(key);
      }
    },
    [check, openSettings, updatePermission],
  );

  // ── Initial check ──────────────────────────────────────────────────────────

  useEffect(() => {
    void checkAll();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // NOTE: no re-check on window focus. Consumers share ONE instance via
  // PermissionsProvider; explicit Refresh buttons and the post-request checks
  // above are the recheck paths. (A focus listener once re-triggered the
  // macOS Sequoia 30-day screen-recording consent on every Settings round trip.)

  const summary = useMemo(() => summarizePermissions(permissions), [permissions]);

  return {
    permissions,
    isLoading,
    summary,
    devicePermissions,
    devicePlatform,
    deviceLastRefresh,
    refreshDevicePermissions,
    check,
    checkAll,
    request,
    openSettings,
  };
}

// ---------------------------------------------------------------------------
// Utility exports
// ---------------------------------------------------------------------------

export function hasRequiredPermissions(
  permissions: Map<PermissionKey, PermissionState>,
  requiredKeys: PermissionKey[],
): boolean {
  return requiredKeys.every((key) => {
    const state = permissions.get(key);
    return state ? isGranted(state.status) : false;
  });
}

export function getFirstMissingPermission(
  permissions: Map<PermissionKey, PermissionState>,
  requiredKeys: PermissionKey[],
): PermissionState | null {
  for (const key of requiredKeys) {
    const state = permissions.get(key);
    if (state && !isGranted(state.status)) return state;
  }
  return null;
}
