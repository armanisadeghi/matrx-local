/**
 * window-role — which window am I, and am I the leader?
 *
 * Multi-window model (labels assigned by Rust, see src-tauri/src/windows.rs):
 *   • `main` / `peer-N`       — FULL app windows (complete provider tree)
 *   • `panel-<page>`          — lightweight single-page windows
 *   • `transcript-overlay`    — the always-on-top overlay
 *   • browser (no Tauri)      — dev fallback, treated as a full/leader window
 *
 * The LEADER is the one full window that runs singleton services (background
 * tasks, cloud heartbeat, auto-update polling). Leadership is decided by Rust
 * (oldest surviving full window) and pushed via the `window-leader-changed`
 * event; this module mirrors it into a subscribable store consumed by
 * `useWindowLeader()`. Never duplicate leadership logic elsewhere.
 */

import { isTauri } from "@/lib/sidecar";

export type PanelPage =
  | "chat"
  | "cloud-chat"
  | "notes"
  | "activity"
  | "ports"
  | "transcription"
  | "tts"
  | "media-generation"
  | "media-gallery";

export type WindowRole =
  | { kind: "main"; label: string }
  | { kind: "peer"; label: string }
  | { kind: "panel"; label: string; page: PanelPage }
  | { kind: "overlay"; label: string }
  | { kind: "browser"; label: "main" };

const PANEL_PREFIX = "panel-";

function detectRole(): WindowRole {
  if (!isTauri()) {
    return { kind: "browser", label: "main" };
  }
  // Synchronous label read — Tauri injects this before any script runs.
  const internals = (
    window as Window & {
      __TAURI_INTERNALS__?: {
        metadata?: { currentWebviewWindow?: { label?: string } };
      };
    }
  ).__TAURI_INTERNALS__;
  const label = internals?.metadata?.currentWebviewWindow?.label ?? "main";

  if (label === "transcript-overlay") return { kind: "overlay", label };
  if (label.startsWith(PANEL_PREFIX)) {
    return {
      kind: "panel",
      label,
      page: label.slice(PANEL_PREFIX.length) as PanelPage,
    };
  }
  if (label.startsWith("peer-")) return { kind: "peer", label };
  return { kind: "main", label };
}

/** This window's role — fixed for the window's lifetime. */
export const windowRole: WindowRole = detectRole();

/** Full windows mount the complete app; panels/overlay mount minimal shells. */
export const isFullWindow =
  windowRole.kind === "main" ||
  windowRole.kind === "peer" ||
  windowRole.kind === "browser";

// ── Leadership store ─────────────────────────────────────────────────────────

// Browser dev is trivially the only window → leader. In Tauri we start false
// and wait for Rust's answer, so two windows can never both believe they lead.
let leader = windowRole.kind === "browser";
const listeners = new Set<() => void>();

function setLeader(value: boolean): void {
  if (value === leader) return;
  leader = value;
  listeners.forEach((cb) => cb());
}

export function isWindowLeader(): boolean {
  return leader;
}

export function subscribeWindowLeader(cb: () => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

if (isTauri() && (windowRole.kind === "main" || windowRole.kind === "peer")) {
  void (async () => {
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      const info = await invoke<{ label: string; kind: string; is_leader: boolean }>(
        "get_window_role",
      );
      setLeader(info.is_leader);
      const { listen } = await import("@tauri-apps/api/event");
      await listen<string>("window-leader-changed", (event) => {
        setLeader(event.payload === windowRole.label);
      });
    } catch (err) {
      // Loud recovery: if role detection breaks, the main window assumes
      // leadership so singleton services never silently die app-wide.
      console.error(
        "[window-role] get_window_role failed — falling back to label-based leadership",
        err,
      );
      setLeader(windowRole.kind === "main");
    }
  })();
}
