/**
 * usePermissions — Unified macOS permissions hook for AI Matrx Desktop.
 *
 * Architecture:
 *
 * All permissions that can be checked/requested from within the Tauri .app
 * process use tauri-plugin-macos-permissions or direct Tauri commands so that
 * macOS TCC associates the grant with the correct principal (the .app bundle,
 * not the Python sidecar).
 *
 * The Python engine REST is only used for status display of things that
 * cannot be checked from the frontend at all (e.g. bluetooth adapter state).
 *
 * Known Apple quirks handled here:
 *
 * - Screen Recording: CGPreflightScreenCaptureAccess() returns false even when
 *   already granted until the app is restarted. We supplement it with a
 *   functional test via the engine to detect this "already granted but preflight
 *   lying" case and mark it as granted.
 *
 * - Camera/Microphone: requestXxxPermission() fires an ObjC async callback and
 *   returns immediately. We wait 800 ms before re-checking so the OS dialog has
 *   time to fire and the user has a moment to respond.
 *
 * - Contacts/Calendar/Photos/Location: These MUST be triggered by the main
 *   .app process. The Python sidecar cannot prompt TCC dialogs on behalf of the
 *   app. We open System Settings directly since we don't have ObjC bindings for
 *   these in the plugin, and rely on the engine for read-only status display.
 *
 * - Input Monitoring / Automation / Local Network: Same — open Settings + rely
 *   on focus-return re-check.
 */

import { useCallback, useEffect, useState } from "react";
import { isTauri } from "@/lib/sidecar";
import { engine, type PermissionInfo } from "@/lib/api";

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
  | "local_network"
  | "automation"
  | "network"
  | "messages"
  | "mail"
  | "speech_recognition";

export type PermissionStatus =
  | "granted"
  | "denied"
  | "not_determined"
  | "restricted"
  | "unavailable"
  | "unknown"
  | "loading";

export interface PermissionState {
  key: PermissionKey;
  status: PermissionStatus;
  label: string;
  description: string;
  tools: string[];
  /** true = plugin can show an in-app OS dialog; false = must go to Settings */
  canPrompt: boolean;
  settingsUrl: string;
  detail?: string;
}

// ---------------------------------------------------------------------------
// Static metadata
// ---------------------------------------------------------------------------

const PERMISSION_META: Record<
  PermissionKey,
  Pick<PermissionState, "label" | "description" | "tools" | "canPrompt" | "settingsUrl">
> = {
  microphone: {
    label: "Microphone",
    description: "Audio recording, live transcription, voice tools",
    tools: ["RecordAudio", "TranscribeAudio", "ListAudioDevices", "PlayAudio"],
    canPrompt: true,
    settingsUrl: "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
  },
  camera: {
    label: "Camera",
    description: "Camera capture for vision and document tools",
    tools: ["CaptureCamera"],
    canPrompt: true,
    settingsUrl: "x-apple.systempreferences:com.apple.preference.security?Privacy_Camera",
  },
  screen_recording: {
    label: "Screen Recording",
    description: "Screenshot tool and screen-based automation",
    tools: ["Screenshot", "BrowserScreenshot"],
    // Screen recording CAN be prompted when not_determined (the OS shows the
    // native "AI Matrx.app would like to record your screen" dialog).
    // Once denied or granted, CGRequestScreenCaptureAccess has no effect —
    // the user must change it in System Settings. The request() function
    // handles this by prompting when not_determined and opening Settings otherwise.
    canPrompt: true,
    settingsUrl: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
  },
  accessibility: {
    label: "Accessibility",
    description: "Keyboard simulation, mouse control, window management",
    tools: ["TypeText", "Hotkey", "MouseClick", "MouseMove", "ListWindows", "FocusWindow", "MoveWindow", "MinimizeWindow", "FocusApp"],
    canPrompt: false,
    settingsUrl: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
  },
  full_disk_access: {
    label: "Full Disk Access",
    description: "Read and write files outside standard app folders",
    tools: ["ReadFile", "WriteFile", "ListDirectory", "SearchFiles", "DeleteFile"],
    canPrompt: false,
    settingsUrl: "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
  },
  input_monitoring: {
    label: "Input Monitoring",
    description: "Global keyboard and mouse event monitoring",
    tools: ["MonitorInput"],
    canPrompt: false,
    settingsUrl: "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
  },
  contacts: {
    label: "Contacts",
    description: "Read and search your address book",
    tools: ["SearchContacts", "GetContact"],
    canPrompt: false,
    settingsUrl: "x-apple.systempreferences:com.apple.preference.security?Privacy_Contacts",
  },
  calendar: {
    label: "Calendar",
    description: "Read and create calendar events",
    tools: ["ListEvents", "CreateEvent"],
    canPrompt: false,
    settingsUrl: "x-apple.systempreferences:com.apple.preference.security?Privacy_Calendars",
  },
  photos: {
    label: "Photos Library",
    description: "Read images from your photo library",
    tools: ["SearchPhotos", "GetPhoto"],
    canPrompt: false,
    settingsUrl: "x-apple.systempreferences:com.apple.preference.security?Privacy_Photos",
  },
  reminders: {
    label: "Reminders",
    description: "Read and create reminders in macOS Reminders",
    tools: ["ListReminders", "CreateReminder"],
    canPrompt: false,
    settingsUrl: "x-apple.systempreferences:com.apple.preference.security?Privacy_Reminders",
  },
  bluetooth: {
    label: "Bluetooth",
    description: "Discover and list nearby Bluetooth devices",
    tools: ["BluetoothDevices", "ConnectedDevices"],
    canPrompt: false,
    settingsUrl: "x-apple.systempreferences:com.apple.preference.security?Privacy_Bluetooth",
  },
  location: {
    label: "Location Services",
    description: "Access current GPS/network location",
    tools: ["GetLocation"],
    canPrompt: false,
    settingsUrl: "x-apple.systempreferences:com.apple.preference.security?Privacy_LocationServices",
  },
  local_network: {
    label: "Local Network",
    description: "Discover devices and services on your local network",
    tools: ["NetworkScan", "MDNSDiscover", "WifiNetworks"],
    canPrompt: false,
    settingsUrl: "x-apple.systempreferences:com.apple.preference.security?Privacy_LocalNetwork",
  },
  automation: {
    label: "Automation (Apple Events)",
    description: "Send commands to other apps via AppleScript",
    tools: ["AppleScript", "LaunchApp", "FocusApp"],
    canPrompt: false,
    settingsUrl: "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation",
  },
  network: {
    label: "Network Access",
    description: "Connect to internet and local network services",
    tools: ["NetworkInfo", "PortScan"],
    canPrompt: false,
    settingsUrl: "x-apple.systempreferences:com.apple.preference.network",
  },
  messages: {
    label: "Messages & iMessage",
    description: "Read iMessage/SMS history and send messages",
    tools: ["ListMessages", "ListConversations", "SendMessage"],
    canPrompt: false,
    // Messages access requires Full Disk Access (to read chat.db) and
    // Automation (to send via Messages.app). Direct the user to both.
    settingsUrl: "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
  },
  mail: {
    label: "Mail",
    description: "Read and send emails via Mail.app",
    tools: ["ListEmails", "SendEmail", "GetEmailAccounts"],
    canPrompt: false,
    settingsUrl: "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation",
  },
  speech_recognition: {
    label: "Speech Recognition",
    description: "Transcribe audio using Apple's on-device speech engine",
    tools: ["TranscribeWithAppleSpeech", "ListSpeechLocales"],
    canPrompt: false,
    settingsUrl: "x-apple.systempreferences:com.apple.preference.security?Privacy_SpeechRecognition",
  },
};

// Keys whose check/request go through tauri-plugin-macos-permissions
const PLUGIN_KEYS = new Set<PermissionKey>([
  "microphone",
  "camera",
  "screen_recording",
  "accessibility",
  "full_disk_access",
  "input_monitoring",
]);

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

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export interface UsePermissionsReturn {
  permissions: Map<PermissionKey, PermissionState>;
  isLoading: boolean;
  /**
   * The engine's raw per-device permission list (Dashboard/Devices rows).
   * ONE shared copy — pages must consume this instead of fetching their own
   * engine.getDevicePermissions() snapshot (the historical private copies
   * were never reconciled with each other or with the plugin results).
   */
  devicePermissions: PermissionInfo[];
  /** Engine-reported platform for the device list ("Darwin", "Windows"...). */
  devicePlatform: string;
  deviceLastRefresh: Date | null;
  refreshDevicePermissions: (force?: boolean) => Promise<void>;
  check: (key: PermissionKey) => Promise<PermissionStatus>;
  checkAll: () => Promise<void>;
  request: (key: PermissionKey) => Promise<void>;
  openSettings: (key: PermissionKey) => Promise<void>;
}

function buildInitialState(): Map<PermissionKey, PermissionState> {
  const map = new Map<PermissionKey, PermissionState>();
  for (const [key, meta] of Object.entries(PERMISSION_META) as [
    PermissionKey,
    (typeof PERMISSION_META)[PermissionKey],
  ][]) {
    map.set(key, { key, status: "loading", ...meta });
  }
  return map;
}

const delay = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

function fallbackPermissionState(key: PermissionKey): PermissionState {
  const label = key
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");

  return {
    key,
    status: "unknown",
    label,
    description: "Permission reported by the engine",
    tools: [],
    canPrompt: false,
    settingsUrl: "x-apple.systempreferences:com.apple.preference.security?Privacy",
  };
}

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
        const next = new Map(prev);
        const current =
          next.get(key) ??
          (PERMISSION_META[key]
            ? { key, status: "loading" as PermissionStatus, ...PERMISSION_META[key] }
            : fallbackPermissionState(key));
        const { detail: _stale, ...rest } = current;
        next.set(key, { ...rest, status, ...(detail !== undefined ? { detail } : {}) });
        return next;
      });
    },
    [],
  );

  /**
   * Check a plugin-native permission.
   *
   * All checks go through tauri-plugin-macos-permissions which calls the
   * correct underlying framework API for each permission type:
   *   - microphone / camera  → AVCaptureDevice.authorizationStatus (AVFoundation)
   *   - screen_recording     → CGPreflightScreenCaptureAccess (CoreGraphics)
   *   - accessibility        → AXIsProcessTrusted
   *   - full_disk_access     → file-system probe
   *   - input_monitoring     → IOKit
   *
   * Plugin limitation — microphone & camera: The plugin only returns a boolean.
   * We persist whether this UI has made an explicit native request, allowing a
   * later false result to be presented as denied instead of "Not Requested"
   * forever. An installation denied outside this app remains indeterminate
   * until the user explicitly tries the grant action.
   *
   * Screen recording: Uses CGPreflightScreenCaptureAccess() — a read-only
   * status query that never triggers a permission dialog. Known limitation:
   * returns false until app restart after an in-session grant. Do NOT use
   * SCShareableContent for status checks — it triggers the macOS Sequoia
   * recurring 30-day consent prompt on every invocation.
   */
  const checkPluginPermission = useCallback(
    async (key: PermissionKey): Promise<PermissionStatus> => {
      if (!isTauri()) return "unavailable";
      try {
        const perms = await import("tauri-plugin-macos-permissions-api");
        let granted: boolean;
        switch (key) {
          case "microphone":
            granted = await perms.checkMicrophonePermission();
            return pluginBooleanPermissionStatus(
              granted,
              wasExplicitlyRequested("microphone"),
            );

          case "camera":
            granted = await perms.checkCameraPermission();
            return pluginBooleanPermissionStatus(
              granted,
              wasExplicitlyRequested("camera"),
            );

          case "screen_recording": {
            // THE ENGINE is authoritative here, not this window: screen capture
            // runs in the Python engine (`screencapture`), and macOS grants the
            // permission per-process — the Tauri app's own preflight answers a
            // question nobody asked. Asking the plugin instead is what let the
            // Setup Wizard (engine-sourced) say "denied" while the Permissions
            // modal (plugin-sourced) said "Not Requested" on the same screen.
            //
            // Safe to call on every checkAll: the engine's check is a read-only
            // CGPreflightScreenCaptureAccess. (It once used SCShareableContent,
            // which ACTIVELY TRIGGERS the macOS Sequoia 30-day consent dialog on
            // every call — never reintroduce that; see checker.py.)
            try {
              const res = (await engine.get(
                "/devices/permissions/screen_recording",
              )) as { status?: string };
              if (res.status === "granted") return "granted";
              if (res.status === "denied") return "denied";
              if (res.status === "not_determined") return "not_determined";
            } catch {
              // Engine not up yet — fall back to this process's own preflight
              // rather than reporting a permission state we cannot know.
            }
            granted = await perms.checkScreenRecordingPermission();
            return granted ? "granted" : "not_determined";
          }

          case "accessibility":
            granted = await perms.checkAccessibilityPermission();
            return granted ? "granted" : "not_determined";

          case "full_disk_access":
            granted = await perms.checkFullDiskAccessPermission();
            return granted ? "granted" : "not_determined";

          case "input_monitoring":
            granted = await perms.checkInputMonitoringPermission();
            return granted ? "granted" : "not_determined";

          default:
            return "unknown";
        }
      } catch {
        return "unknown";
      }
    },
    [],
  );

  // ── Check ──────────────────────────────────────────────────────────────────

  const check = useCallback(
    async (key: PermissionKey): Promise<PermissionStatus> => {
      if (PLUGIN_KEYS.has(key)) {
        const status = await checkPluginPermission(key);
        updatePermission(key, status);
        return status;
      }
      try {
        const result = await engine.getDevicePermission(key);
        const status = result.status as PermissionStatus;
        updatePermission(key, status, result.details);
        return status;
      } catch {
        updatePermission(key, "unknown");
        return "unknown";
      }
    },
    [checkPluginPermission, updatePermission],
  );

  const checkAll = useCallback(async () => {
    setIsLoading(true);

    const pluginChecks = Array.from(PLUGIN_KEYS).map(async (key) => {
      const status = await checkPluginPermission(key);
      updatePermission(key, status);
    });

    const engineCheck = (async () => {
      try {
        const result = await engine.getDevicePermissions();
        setDevicePermissions(result.permissions);
        setDevicePlatform(result.platform);
        setDeviceLastRefresh(new Date());
        for (const p of result.permissions) {
          const key = p.permission as PermissionKey;
          if (!PLUGIN_KEYS.has(key)) {
            updatePermission(key, p.status as PermissionStatus, p.details);
          }
        }
      } catch {
        const engineKeys = Object.keys(PERMISSION_META).filter(
          (k) => !PLUGIN_KEYS.has(k as PermissionKey),
        ) as PermissionKey[];
        for (const key of engineKeys) {
          updatePermission(key, "unknown");
        }
      }
    })();

    await Promise.all([...pluginChecks, engineCheck]);
    setIsLoading(false);
  }, [checkPluginPermission, updatePermission]);

  /**
   * Refresh the shared engine device-permission list (Dashboard/Devices).
   * `force` bypasses the engine's TTL cache — use from explicit "Refresh"
   * affordances only. Also folds statuses back into the permission Map so
   * every consumer stays consistent.
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
          if (!PLUGIN_KEYS.has(key)) {
            updatePermission(key, p.status as PermissionStatus, p.details);
          }
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
      if (!isTauri()) {
        await openSettings(key);
        return;
      }

      switch (key) {
        // ── Microphone & Camera: AVFoundation in-app dialog on first request ──
        case "microphone":
        case "camera": {
          try {
            const perms = await import("tauri-plugin-macos-permissions-api");
            markExplicitlyRequested(key);
            if (key === "microphone") {
              await perms.requestMicrophonePermission();
            } else {
              await perms.requestCameraPermission();
            }
            // Wait for the async AVFoundation callback to settle before re-checking
            await delay(POST_REQUEST_DELAY_MS);
            await check(key);
          } catch (err) {
            console.error(`[permissions] Failed to request ${key}:`, err);
            await openSettings(key);
          }
          break;
        }

        // ── Screen Recording: ask the ENGINE first, then open System Settings ──
        // Screen capture runs in the Python engine (`screencapture`), so the
        // ENGINE is the process that needs the grant — and until it calls
        // CGRequestScreenCaptureAccess once, macOS never lists it under Screen
        // Recording. Sending the user straight to System Settings (what this
        // used to do) sent them to a pane with nothing to switch on.
        //
        // The engine's request registers it and shows the native prompt; we then
        // still open System Settings, because on macOS Sequoia a grant only
        // takes effect on the next app launch and the user may need to flip the
        // switch there.
        case "screen_recording": {
          try {
            await engine.post("/devices/permissions/request/screen-recording", {});
          } catch (err) {
            console.error("[permissions] Engine screen-recording request failed:", err);
          }
          await delay(POST_REQUEST_DELAY_MS);
          await check(key);
          await openSettings(key);
          break;
        }

        case "contacts":
        case "calendar":
        case "reminders":
        case "photos":
        case "location":
        case "speech_recognition": {
          try {
            await engine.post(`/devices/permissions/request/${key}`, {});
            await delay(POST_REQUEST_DELAY_MS);
            const status = await check(key);
            if (status !== "granted") await openSettings(key);
          } catch (err) {
            console.error(`[permissions] Engine request for ${key} failed:`, err);
            await openSettings(key);
          }
          break;
        }

        // ── Accessibility, Full Disk Access, Input Monitoring: open Settings ──
        // These cannot be prompted with an in-app dialog — the plugin's request
        // calls open System Settings directly, same as openSettings().
        case "accessibility":
        case "full_disk_access":
        case "input_monitoring": {
          try {
            const perms = await import("tauri-plugin-macos-permissions-api");
            if (key === "accessibility") {
              await perms.requestAccessibilityPermission();
            } else if (key === "full_disk_access") {
              await perms.requestFullDiskAccessPermission();
            } else {
              await perms.requestInputMonitoringPermission();
            }
          } catch (err) {
            console.error(`[permissions] Failed to open settings for ${key}:`, err);
            await openSettings(key);
          }
          break;
        }

        // ── All others: open the specific System Settings pane directly ───────
        // Contacts, Calendar, Reminders, Photos, Location, Local Network,
        // Automation, Network, Messages, Mail, Speech Recognition — these require
        // ObjC frameworks not exposed by the plugin. Opening System Settings is
        // the correct action.
        default:
          await openSettings(key);
          break;
      }
    },
    [check, openSettings],
  );

  // ── Initial check ──────────────────────────────────────────────────────────

  useEffect(() => {
    checkAll();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // NOTE: We intentionally do NOT re-check permissions on window focus.
  //
  // The hook is instantiated by multiple components simultaneously (Dashboard,
  // Voice, Devices, PermissionsModal, SetupWizard). A focus listener here would
  // fire checkAll() N times in parallel on every focus event — once per mounted
  // consumer. With the old SCShareableContent-based screen recording check, this
  // triggered the macOS Sequoia 30-day consent dialog on every System Settings
  // round-trip, causing repeated prompts.
  //
  // The Refresh button in PermissionsModal and Devices pages provides a manual
  // recheck path. macOS TCC status for CGPreflightScreenCaptureAccess only
  // updates after an app restart anyway, so auto-recheck provides no real value.

  return {
    permissions,
    isLoading,
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

export function isGranted(status: PermissionStatus): boolean {
  return status === "granted";
}

export function hasRequiredPermissions(
  permissions: Map<PermissionKey, PermissionState>,
  requiredKeys: PermissionKey[],
): boolean {
  return requiredKeys.every((key) => permissions.get(key)?.status === "granted");
}

export function getFirstMissingPermission(
  permissions: Map<PermissionKey, PermissionState>,
  requiredKeys: PermissionKey[],
): PermissionState | null {
  for (const key of requiredKeys) {
    const state = permissions.get(key);
    if (state && state.status !== "granted") return state;
  }
  return null;
}

export { PLUGIN_KEYS, PERMISSION_META };
