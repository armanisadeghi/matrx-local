/**
 * THE version helper. Every surface that shows a version or claims "up to
 * date" derives it from here — About, the update banner and dialog, the status
 * bar, the startup and first-run screens, the login footer.
 *
 * Why it exists (measured 2026-09-17): the updater installed 1.4.147 into
 * `/Applications/AI Matrx.app` at 03:30 while the running desktop process and
 * its engine were still 1.4.145. Settings → About showed one unlabelled
 * "Version", and "Check for updates" answered "No updates available" — true of
 * the disk, false of what was running, and nothing on screen said a restart
 * was needed. A version with no label cannot be honest, because there are
 * three different true answers at once:
 *
 *   Running          — the build executing right now. Baked into the binary
 *                      and the renderer bundle at compile time; cannot change
 *                      without a restart.
 *   Installed on disk — the build that would launch next. The updater replaces
 *                      it underneath a running process.
 *   Latest available — what the update endpoint offers.
 *
 * The rules this module enforces:
 *   1. No screen may say "latest"/"up to date" while running ≠ installed.
 *   2. Running ≠ installed is announced loudly, with a one-click restart, and
 *      is a derived truth — not a notification, so it survives a dismissal, a
 *      renderer reload and a new window.
 *   3. An unknown fact renders as unknown, never as agreement.
 *   4. Engine build ≠ desktop build is a fact on screen, not a silent state
 *      (they legitimately differ mid-update: the engine keeps serving its old
 *      build until it is respawned).
 */

/** The updater's own verdict, as reported by the Rust `check_for_updates`. */
export type UpdaterStatus = "up_to_date" | "available" | "downloading" | "installed";

export interface VersionStateInput {
  /** Compile-time identity of the renderer bundle now executing. */
  runningDesktop: string;
  /** `/health` version of the engine process now serving. `null` = not reached. */
  runningEngine?: string | null;
  /** Bundle `Info.plist` version, re-read from disk. `null` = unreadable. */
  installedOnDisk?: string | null;
  /** Version the updater last offered, when it offered one. */
  latestAvailable?: string | null;
  /** The updater's last verdict, or `null` when it has not been asked yet. */
  updaterStatus?: UpdaterStatus | null;
}

export interface VersionFact {
  /** Stable key for tests and React keys. */
  key: "running" | "installed" | "latest" | "engine";
  /** The label the user reads. Never omitted — an unlabelled version lies. */
  label: string;
  /** The version, or `null` when genuinely unknown. */
  value: string | null;
  /** Shown when `value` is `null`, or as a qualifier. */
  detail?: string;
  /** True when this fact disagrees with the running build. */
  differsFromRunning?: boolean;
}

export interface VersionState {
  /** The three build facts, plus the engine fact when the engine is reachable. */
  facts: VersionFact[];
  /**
   * A newer build is sitting on disk and only a restart will run it. Derived
   * from running ≠ installed, so it is true again on the next render, in every
   * window, after any dismissal.
   */
  restartRequired: boolean;
  /** The build that a restart would start. `null` when not known. */
  pendingVersion: string | null;
  /** The engine is serving a different build than the desktop is running. */
  engineMismatch: boolean;
  /** Loud headline for the restart state; `null` when no restart is pending. */
  restartHeadline: string | null;
  /**
   * The ONE sentence a surface may use to describe update state. It never says
   * "latest" while a restart is pending.
   */
  updateSummary: string;
}

const RESTART_HEADLINE = "Update installed — restart AI Matrx to finish";

/** Non-empty, trimmed, or `null`. Empty strings are unknowns, not versions. */
function clean(v: string | null | undefined): string | null {
  const t = typeof v === "string" ? v.trim() : "";
  return t.length > 0 ? t : null;
}

/** Tolerate a leading `v` on either side — `v1.4.147` is `1.4.147`. */
function sameVersion(a: string | null, b: string | null): boolean {
  if (a === null || b === null) return false;
  const norm = (s: string) => s.trim().replace(/^v/i, "");
  return norm(a) === norm(b);
}

export function deriveVersionState(input: VersionStateInput): VersionState {
  const running = clean(input.runningDesktop);
  const installed = clean(input.installedOnDisk);
  const engine = clean(input.runningEngine);
  const offered = clean(input.latestAvailable);
  const status = input.updaterStatus ?? null;

  // A restart is required when the disk has moved on without us. The updater
  // reporting "installed" says the same thing — but only for as long as that
  // in-memory status lives, so it is a secondary signal, never the only one.
  const diskAhead = installed !== null && running !== null && !sameVersion(installed, running);
  const restartRequired = diskAhead || status === "installed";
  const pendingVersion = diskAhead
    ? installed
    : status === "installed"
      ? (offered ?? installed)
      : null;

  const engineMismatch = engine !== null && running !== null && !sameVersion(engine, running);

  // "Latest available" is what the updater offered. When it says up_to_date it
  // is speaking about the disk, so the disk version is the latest it knows of.
  const latestValue =
    offered ?? (status === "up_to_date" ? (installed ?? running) : null);

  const facts: VersionFact[] = [
    {
      key: "running",
      label: "Running",
      value: running,
      detail: "The build executing right now",
    },
    {
      key: "installed",
      label: "Installed on disk",
      value: installed,
      ...(installed === null
        ? { detail: "Cannot be read on this platform" }
        : { detail: diskAhead ? "Starts on next launch" : "Same as running" }),
      differsFromRunning: diskAhead,
    },
    {
      key: "latest",
      label: "Latest available",
      value: latestValue,
      ...(latestValue === null
        ? { detail: status === null ? "Not checked yet" : "Unknown" }
        : {}),
    },
  ];

  if (engine !== null || engineMismatch) {
    facts.push({
      key: "engine",
      label: "Engine build",
      value: engine,
      ...(engineMismatch
        ? {
            detail:
              "Different from the desktop build — the engine keeps serving its own build until it restarts",
          }
        : {}),
      differsFromRunning: engineMismatch,
    });
  }

  let updateSummary: string;
  if (restartRequired) {
    updateSummary = pendingVersion
      ? `${RESTART_HEADLINE} (v${pendingVersion.replace(/^v/i, "")} is installed; you are running ${running ?? "an older build"})`
      : RESTART_HEADLINE;
  } else if (status === "downloading") {
    updateSummary = "Downloading update…";
  } else if (status === "available" && offered) {
    updateSummary = `v${offered.replace(/^v/i, "")} available — preparing in the background; use Install when ready`;
  } else if (status === "up_to_date") {
    updateSummary = "You're running the latest version";
  } else {
    updateSummary = "Check for new releases";
  }

  return {
    facts,
    restartRequired,
    pendingVersion,
    engineMismatch,
    restartHeadline: restartRequired ? RESTART_HEADLINE : null,
    updateSummary,
  };
}
