/**
 * Bounded status/recovery panel for the shared image/video managed runtime.
 *
 * Runtime lifecycle is owned once by MediaGenContext. This component never
 * polls, opens a stream, stores terminal success, or infers health from package
 * versions. It intentionally renders nothing when the authoritative state is
 * ready, so a success card can never cover the generation surface.
 */

import {
  AlertCircle,
  Loader2,
  PackagePlus,
  RefreshCw,
  RotateCcw,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import {
  isRuntimeActive,
  runtimeAction,
} from "@/lib/image-gen/runtime-state";

/** Kept for call-site compatibility while layouts converge on the shared panel. */
export interface InstallerPreviewModel {
  model_id: string;
  name: string;
  provider: string;
  description: string;
  vram_gb: number;
  ram_gb: number;
  requires_hf_token: boolean;
}

export function ImageGenInstaller({
  headline,
  intro,
}: {
  models?: InstallerPreviewModel[];
  onInstallComplete?: () => void;
  headline?: string;
  intro?: string;
  upgrade?: boolean;
}) {
  const [state, actions] = useMediaGenApp();
  const { mediaRuntime, mediaRuntimeLoading, mediaRuntimeError } = state;
  const {
    ensureMediaRuntime,
    repairMediaRuntime,
    restartMediaRuntime,
    refreshMediaRuntime,
  } = actions;

  if (mediaRuntime?.state === "ready") return null;

  const active = isRuntimeActive(mediaRuntime);
  const action = runtimeAction(mediaRuntime);
  const failed =
    mediaRuntime?.state === "failed" || mediaRuntime?.state === "rolled_back";
  const title = active
    ? mediaRuntime?.operation === "repair"
      ? "Repairing AI runtime…"
      : mediaRuntime?.operation === "update"
        ? "Updating AI runtime…"
        : "Installing AI runtime…"
    : failed
      ? "AI runtime needs repair"
      : mediaRuntime?.state === "restart_required"
        ? "Restart required to finish setup"
        : (headline ?? "Set up on-device image generation");
  const message =
    mediaRuntime?.failure_detail ||
    mediaRuntime?.message ||
    mediaRuntimeError ||
    intro ||
    "The app will install and validate the complete managed AI runtime before generation is enabled.";

  return (
    <section
      className={`rounded-xl border px-4 py-4 ${failed ? "border-destructive/40 bg-destructive/5" : "border-violet-500/35 bg-violet-500/5"}`}
      aria-live="polite"
      data-runtime-state={mediaRuntime?.state ?? "unknown"}
    >
      <div className="flex items-start gap-3">
        <div className="mt-0.5 rounded-lg bg-background/70 p-2">
          {active || mediaRuntimeLoading ? (
            <Loader2 className="h-4 w-4 animate-spin text-violet-500" />
          ) : failed ? (
            <AlertCircle className="h-4 w-4 text-destructive" />
          ) : (
            <PackagePlus className="h-4 w-4 text-violet-500" />
          )}
        </div>
        <div className="min-w-0 flex-1 space-y-2.5">
          <div>
            <p className="text-sm font-semibold">{title}</p>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
              {message}
            </p>
            {mediaRuntime?.failure_code && (
              <p className="mt-1 break-all font-mono text-[10px] text-destructive">
                {mediaRuntime.failure_code}
              </p>
            )}
          </div>

          {active && (
            <div className="space-y-1.5">
              <div className="flex justify-between text-[11px] text-muted-foreground">
                <span>{mediaRuntime?.stage || "Preparing…"}</span>
                <span className="font-mono tabular-nums">
                  {Math.round(mediaRuntime?.percent ?? 0)}%
                </span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-violet-500 transition-[width] duration-300"
                  style={{ width: `${Math.max(0, Math.min(100, mediaRuntime?.percent ?? 0))}%` }}
                />
              </div>
            </div>
          )}

          {!active && (
            <div className="flex flex-wrap gap-2">
              {(action === "install" || action === "update") && (
                <Button size="sm" onClick={() => void ensureMediaRuntime()}>
                  <PackagePlus className="mr-1.5 h-3.5 w-3.5" />
                  {action === "update" ? "Update and validate" : "Install and validate"}
                </Button>
              )}
              {action === "repair" && (
                <Button size="sm" onClick={() => void repairMediaRuntime()}>
                  <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
                  Repair and fully validate
                </Button>
              )}
              {action === "restart" && (
                <Button size="sm" onClick={() => void restartMediaRuntime()}>
                  <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
                  Restart engine and finish
                </Button>
              )}
              {(mediaRuntimeError || mediaRuntime === null) && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => void refreshMediaRuntime()}
                >
                  <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
                  Check again
                </Button>
              )}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
