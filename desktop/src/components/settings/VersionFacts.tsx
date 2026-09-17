/**
 * The version block of Settings → About.
 *
 * Its whole job is to make three simultaneous truths readable at a glance —
 * what is running, what is installed on disk, what is available — plus the
 * engine's own build, which legitimately differs mid-update. When running and
 * installed disagree, the restart state is the loudest thing on the panel and
 * carries the remedy.
 *
 * Kept out of `pages/Settings.tsx` so it can be rendered on its own by the
 * guard (`VersionFacts.test.tsx`) that proves the lying-screen states can never
 * come back.
 */

import { Badge, Button } from "@ai-matrx/design-system";
import { AlertCircle, Loader2, RefreshCw } from "lucide-react";
import type { VersionState } from "@/lib/version-facts";

export interface VersionFactsProps {
  versions: VersionState;
  /** True while the graceful restart sequence is already running. */
  restarting: boolean;
  /** The existing ownership-safe restart path (Rust `restart_app`). */
  onRestart: () => void;
}

export function VersionFacts({ versions, restarting, onRestart }: VersionFactsProps) {
  const runningValue = versions.facts.find((f) => f.key === "running")?.value;

  return (
    <>
      {versions.restartRequired && (
        <div
          className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3"
          role="status"
          aria-live="polite"
          data-testid="restart-required-banner"
        >
          <div className="flex items-start gap-3">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold">{versions.restartHeadline}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {versions.pendingVersion
                  ? `Version ${versions.pendingVersion.replace(/^v/i, "")} is installed on disk. This window keeps running ${runningValue ?? "the previous build"} until AI Matrx restarts.`
                  : "A newer build is installed on disk. This window keeps running the previous build until AI Matrx restarts."}
              </p>
            </div>
            <Button size="sm" disabled={restarting} onClick={onRestart}>
              {restarting ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Restarting…
                </>
              ) : (
                <>
                  <RefreshCw className="h-4 w-4" />
                  Restart now
                </>
              )}
            </Button>
          </div>
        </div>
      )}

      {versions.facts.map((fact) => (
        <div
          key={fact.key}
          className="flex items-start justify-between gap-3"
          data-testid={`version-fact-${fact.key}`}
        >
          <div className="min-w-0">
            <span className="text-sm text-muted-foreground">{fact.label}</span>
            {fact.detail && (
              <p className="mt-0.5 text-xs text-muted-foreground/80">{fact.detail}</p>
            )}
          </div>
          <Badge
            variant={fact.differsFromRunning ? "outline" : "secondary"}
            className={
              fact.differsFromRunning
                ? "shrink-0 border-amber-500/50 text-amber-700 dark:text-amber-400"
                : "shrink-0"
            }
          >
            {fact.value ?? "—"}
          </Badge>
        </div>
      ))}
    </>
  );
}
