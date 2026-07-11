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
import { engine } from "@/lib/api";

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

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export interface UsePermissionsReturn {
  permissions: Map<PermissionKey, PermissionState>;
  isLoading: boolean;
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

export function usePermissions(): UsePermissionsReturn {
  const [permissions, setPermissions] = useState<Map<PermissionKey, PermissionState>>(
    buildInitialState,
  );
  const [isLoading, setIsLoading] = useState(true);

  // ── Helpers ────────────────────────────────────────────────────────────────

  const updatePermission = useCallback(
    (key: PermissionKey, status: PermissionStatus, detail?: string) => {
      setPermissions((prev) => {
        const next = new Map(prev);
        const current = next.get(key)!;
        next.set(key, { ...current, status, ...(detail !== undefined ? { detail } : {}) });
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
   * Plugin limitation — microphone & camera: The plugin only returns a boolean
   * (granted / not-granted). It cannot distinguish between NOT_DETERMINED,
   * DENIED, and RESTRICTED — all three map to "not_determined" here. This is a
   * known limitation of tauri-plugin-macos-permissions v2.3.0. Until the plugin
   * exposes the raw AVAuthorizationStatus integer, users who have explicitly
   * denied mic/camera access will see "Not Requested" rather than "Denied" in
   * the UI. The request() flow still correctly directs denied users to System
   * Settings after the first failed prompt.
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
            // Plugin returns boolean only — see limitation note above.
            granted = await perms.checkMicrophonePermission();
            return granted ? "granted" : "not_determined";

          case "camera":
            // Plugin returns boolean only — see limitation note above.
            granted = await perms.checkCameraPermission();
            return granted ? "granted" : "not_determined";

          case "screen_recording": {
            // CGPreflightScreenCaptureAccess() — read-only status query, no prompt.
            // Known limitation: returns false until app restart after an in-session
            // grant. This is acceptable — the user will see correct status on next launch.
            //
            // DO NOT cross-check via engine.getDevicePermission("screen_recording"):
            // the engine previously used SCShareableContent which ACTIVELY TRIGGERS
            // the macOS Sequoia recurring 30-day screen recording consent dialog
            // every time it is called. That caused repeated prompts on every checkAll().
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

        // ── Screen Recording: always open System Settings ──────────────────────
        // On macOS Sequoia (15+), the requestScreenRecordingPermission() plugin
        // call opens System Settings — there is no longer a native in-app dialog.
        // The plugin's check uses CGPreflightScreenCaptureAccess() which returns
        // false until the next app restart even when the user has already granted
        // access, so we always send the user to System Settings to confirm/change.
        case "screen_recording":
          await openSettings(key);
          break;

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

  return { permissions, isLoading, check, checkAll, request, openSettings };
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
