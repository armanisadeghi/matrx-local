/**
 * use-access-health — THE single frontend owner of filesystem access-health
 * state (GET /access/health / POST /access/recheck / POST /access/reset).
 *
 * One poll owner for the whole app. Historically the global banner (15s
 * active probe) and the Documents prompt (10s) each kept their own unguarded
 * useState copy of the same engine state; a slow stale response could
 * overwrite a newer result and re-show a cleared "Full Disk Access" prompt.
 * This hook fixes both: one store, generation-fenced updates, degraded-aware
 * cadence.
 *
 * Copy contract (deriveAccessPresentation): the definitive "grant Full Disk
 * Access" claim renders ONLY when BOTH macOS process identities agree it is
 * denied: the Tauri parent app and the engine helper. A helper denial alone
 * cannot diagnose the parent app's TCC grant. Anything else gets
 * evidence-based wording. Windows/Linux never mention FDA.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  engine,
  type AccessHealth,
  type AccessResourceHealth,
} from "@/lib/api";
import type { EngineStatus } from "@/hooks/use-engine";

/** Cadence: cheap cached reads while healthy, active probes while degraded
 * so the prompt auto-clears the moment the user grants access. */
const HEALTHY_POLL_MS = 60_000;
const DEGRADED_POLL_MS = 10_000;

export interface AccessHealthUiState {
  health: AccessHealth | null;
  /** Whether `health` came from the cached GET or an active probe POST. */
  source: "cache" | "probe" | null;
  fetchedAt: number | null;
  /** A user-triggered recheck/reset is in flight. */
  checking: boolean;
  /**
   * The Tauri parent-app FDA probe (Stocks/Safari read_dir) — a labeled
   * HINT only; it never claims denial by itself. null = not run / non-mac.
   */
  parentFdaProbe: boolean | null;
}

export interface AccessHealthActions {
  refresh: () => Promise<void>;
  recheck: (opts?: {
    resourceIds?: string[];
    createMissing?: boolean;
  }) => Promise<AccessHealth | null>;
  reset: () => Promise<AccessHealth | null>;
}

export interface UseAccessHealthReturn extends AccessHealthUiState {
  degraded: boolean;
  /** Convenience view of the canonical notes resource. */
  notesResource: AccessResourceHealth | null;
  degradedResources: AccessResourceHealth[];
  actions: AccessHealthActions;
}

export const NOTES_RESOURCE_ID = "notes-canonical";

export function useAccessHealth(engineStatus: EngineStatus): UseAccessHealthReturn {
  const [state, setState] = useState<AccessHealthUiState>({
    health: null,
    source: null,
    fetchedAt: null,
    checking: false,
    parentFdaProbe: null,
  });
  // Generation fence: every fetch takes a ticket; only the CURRENT ticket may
  // write state. Bumped on engine disconnect and reset so in-flight responses
  // from the old world are rejected instead of clobbering newer state.
  const genRef = useRef(0);

  const apply = useCallback(
    (gen: number, health: AccessHealth, source: "cache" | "probe") => {
      if (gen !== genRef.current) return;
      setState((prev) => ({
        ...prev,
        health,
        source,
        fetchedAt: Date.now(),
      }));
    },
    [],
  );

  const refresh = useCallback(async () => {
    if (engineStatus !== "connected") return;
    const gen = genRef.current;
    try {
      const health = await engine.getAccessHealth();
      apply(gen, health, "cache");
    } catch {
      // Transient outage / older engine: keep the last known state rather
      // than flashing banner noise.
    }
  }, [engineStatus, apply]);

  const recheck = useCallback(
    async (opts?: { resourceIds?: string[]; createMissing?: boolean }) => {
      if (engineStatus !== "connected") return null;
      const gen = genRef.current;
      setState((prev) => ({ ...prev, checking: true }));
      try {
        const health = await engine.recheckAccess(opts);
        apply(gen, health, "probe");
        return health;
      } catch {
        return null;
      } finally {
        setState((prev) => ({ ...prev, checking: false }));
      }
    },
    [engineStatus, apply],
  );

  const reset = useCallback(async () => {
    if (engineStatus !== "connected") return null;
    genRef.current += 1; // reject every in-flight response from before the reset
    const gen = genRef.current;
    setState((prev) => ({ ...prev, checking: true }));
    try {
      const health = await engine.resetAccessHealth();
      apply(gen, health, "probe");
      return health;
    } catch {
      return null;
    } finally {
      setState((prev) => ({ ...prev, checking: false }));
    }
  }, [engineStatus, apply]);

  const degraded = state.health?.degraded === true;

  // Init + connection lifecycle: fetch once on connect; clear + fence on
  // disconnect (init fetch lives in the hook per repo React rules).
  useEffect(() => {
    if (engineStatus !== "connected") {
      genRef.current += 1;
      setState({
        health: null,
        source: null,
        fetchedAt: null,
        checking: false,
        parentFdaProbe: null,
      });
      return;
    }
    void refresh();
    // refresh is stable per engineStatus; deliberately not re-run otherwise.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [engineStatus]);

  // Poll — narrowly gated on the two booleans that matter.
  useEffect(() => {
    if (engineStatus !== "connected") return;
    const id = window.setInterval(
      () => {
        if (degraded) {
          const gen = genRef.current;
          void engine
            .recheckAccess()
            .then((health) => apply(gen, health, "probe"))
            .catch(() => undefined);
        } else {
          void refresh();
        }
      },
      degraded ? DEGRADED_POLL_MS : HEALTHY_POLL_MS,
    );
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [engineStatus, degraded]);

  // Parent-app FDA probe (labeled hint): once on connect, darwin only.
  useEffect(() => {
    if (engineStatus !== "connected") return;
    let cancelled = false;
    void (async () => {
      try {
        const { isTauri } = await import("@/lib/sidecar");
        if (!isTauri()) return;
        const perms = await import("tauri-plugin-macos-permissions-api");
        const granted = await perms.checkFullDiskAccessPermission();
        if (!cancelled) {
          setState((prev) => ({ ...prev, parentFdaProbe: granted }));
        }
      } catch {
        // Non-mac or plugin unavailable — stays null (unknown).
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [engineStatus]);

  const actions = useMemo(
    () => ({ refresh, recheck, reset }),
    [refresh, recheck, reset],
  );

  const notesResource =
    state.health?.resources.find((r) => r.resource_id === NOTES_RESOURCE_ID) ??
    null;
  const degradedResources =
    state.health?.resources.filter((r) => r.status === "degraded") ?? [];

  return {
    ...state,
    degraded,
    notesResource,
    degradedResources,
    actions,
  };
}

// ── Presentation ─────────────────────────────────────────────────────────────

export interface AccessPresentation {
  title: string;
  body: string;
  /** Which primary action fits the evidence. */
  primaryAction: "open_settings" | "create_folder" | "check_again";
  /** True only when the FDA remediation (System Settings deep link) is justified. */
  showFdaAction: boolean;
  /** Contextual states stay on the owning page instead of becoming app-wide alerts. */
  scope: "global" | "contextual";
}

/**
 * Pure evidence → copy mapping. The FDA *claim* appears ONLY when the engine
 * helper and Tauri parent app both report denial. macOS TCC evaluates process
 * identities independently, so a helper-only denial cannot prove that the
 * visible desktop app lacks its already-granted permission. Windows and Linux
 * never see the words "Full Disk Access".
 */
export function deriveAccessPresentation(
  resource: AccessResourceHealth,
  health: Pick<AccessHealth, "platform" | "fda">,
  parentFdaProbe: boolean | null,
): AccessPresentation {
  const isMapped = resource.provenance === "mapped";
  const where = resource.root;

  if (resource.kind === "missing_dir") {
    return {
      title: isMapped ? "A mapped folder is missing" : "Your notes folder is missing",
      body: `${resource.label} is missing at ${where}.${
        isMapped ? " Reconnect the drive or remap it in Settings." : ""
      }`,
      primaryAction: "create_folder",
      showFdaAction: false,
      scope: "global",
    };
  }

  if (health.platform !== "darwin") {
    return {
      title: isMapped
        ? "A mapped folder can't be accessed"
        : "Matrx can't access your notes folder",
      body: `${resource.message} Adjust the folder's permissions and check again.`,
      primaryAction: "check_again",
      showFdaAction: false,
      scope: "global",
    };
  }

  const engineFdaDenied = health.fda?.status === "denied";
  const fdaDenied = engineFdaDenied && parentFdaProbe === false;
  if (fdaDenied && !isMapped) {
    return {
      title: "Matrx needs Full Disk Access",
      body:
        `macOS Privacy controls are blocking ${where}. Grant Full Disk ` +
        "Access in System Settings so notes can sync.",
      primaryAction: "open_settings",
      showFdaAction: true,
      scope: "global",
    };
  }

  if (isMapped) {
    return {
      title: "A mapped folder can't be accessed",
      body: `${resource.message} Check the folder's permissions or remap it in Settings.`,
      primaryAction: "check_again",
      showFdaAction: false,
      scope: "global",
    };
  }

  // The helper and parent are separate macOS TCC principals. A helper-only
  // denial is evidence that this operation failed in the helper, NOT proof
  // that the user must grant Full Disk Access to the already-authorized app.
  // Do not reuse resource.message here: the backend cannot see the parent
  // result, so that string may contain the same false FDA diagnosis.
  if (engineFdaDenied) {
    const failedCapabilities = Object.entries(resource.capabilities)
      .filter(([, observation]) => !observation.ok)
      .map(([capability]) => capability.replace(/_/g, " "))
      .join(", ");
    return {
      title: "Matrx needs to recheck your notes folder",
      body:
        `The notes engine reported a denied ${failedCapabilities || "filesystem"} ` +
        `operation at ${resource.last_failure?.path || where}, but that does not ` +
        "prove the Matrx app lacks Full Disk Access. Check again to refresh the " +
        "engine's access state.",
      primaryAction: "check_again",
      showFdaAction: false,
      scope: "contextual",
    };
  }

  // darwin, cause not positively established.
  const hedge =
    parentFdaProbe === false
      ? " Full Disk Access appears not to be granted for Matrx."
      : "";
  return {
    title: "Matrx can't access your notes folder",
    body: `${resource.message}${hedge}`,
    primaryAction: "check_again",
    // Unestablished cause still deserves the settings shortcut on macOS —
    // but as a secondary option, not a definitive claim.
    showFdaAction: true,
    scope: "global",
  };
}
