/**
 * Readiness gates for the media-gen surfaces — the ONE implementation of the
 * loading / engine-error / installer / hardware / outdated-packages states
 * that every layout previously re-implemented (with drift: Workspace had a
 * simplified error card, Gallery an inline banner). All layouts now share the
 * same classified error card.
 */

import { useNavigate } from "react-router-dom";
import type { ReactNode } from "react";
import {
  AlertCircle,
  KeyRound,
  Loader2,
  MonitorX,
  PackagePlus,
  RefreshCw,
  UserPlus,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/hooks/use-auth";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import { engine } from "@/lib/api";
import { ImageGenInstaller } from "@/components/media-gen/ImageGenInstaller";

/**
 * Classify an image-gen status error into a user-actionable card. Canonical —
 * previously private to ImageGenSection while other layouts showed raw text.
 */
export function classifyImageGenLoadError(
  message: string,
  engineConnected: boolean,
): { title: string; hint: string; kind: "engine" | "auth" | "generic" } {
  if (!engineConnected) {
    return {
      title: "Local engine not connected",
      hint: "The Matrx engine on your computer is not reachable yet. Wait for it to finish starting, or restart the app. Then use Try again below.",
      kind: "engine",
    };
  }
  const lower = message.toLowerCase();
  if (
    lower.includes("authorization") ||
    lower.includes("401") ||
    lower.includes("bearer")
  ) {
    return {
      title: "Sign in to Matrx required",
      hint: "This feature talks to the secure copy of the engine on your device. That requires an active Matrx account session (the same sign-in as the rest of the app). Your Hugging Face read token (for gated image checkpoints) is saved under Settings → API keys — not on the Configurations page.",
      kind: "auth",
    };
  }
  return {
    title: "Could not load image generation",
    hint: message,
    kind: "generic",
  };
}

export function CenteredSpinner({ text }: { text: string }) {
  return (
    <div className="flex items-center justify-center py-20 gap-3 text-muted-foreground">
      <Loader2 className="h-5 w-5 animate-spin" />
      <span className="text-sm">{text}</span>
    </div>
  );
}

/**
 * The classified image-gen status error card (engine / auth / generic) with
 * the recovery actions. Shared by every layout.
 */
export function ImageStatusErrorCard({
  error,
  onRetry,
}: {
  error: string;
  onRetry: () => void;
}) {
  const navigate = useNavigate();
  const { isAuthenticated, signInWithOAuth } = useAuth();
  const engineConnected = !!engine.engineUrl;
  const { title, hint, kind } = classifyImageGenLoadError(
    error,
    engineConnected,
  );
  return (
    <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-4 space-y-3">
      <div className="flex items-start gap-3">
        <AlertCircle className="h-4 w-4 text-destructive mt-0.5 shrink-0" />
        <div className="text-sm space-y-1.5 min-w-0">
          <p className="font-medium text-foreground">{title}</p>
          <p className="text-muted-foreground text-xs leading-relaxed">
            {hint}
          </p>
          {kind !== "auth" && (
            <p className="text-[11px] font-mono text-muted-foreground/90 break-all pt-0.5">
              {error}
            </p>
          )}
        </div>
      </div>
      <div className="flex flex-wrap gap-2 pt-1">
        <Button size="sm" variant="secondary" onClick={onRetry}>
          <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
          Try again
        </Button>
        {kind === "auth" && (
          <>
            {!isAuthenticated ? (
              <Button size="sm" onClick={() => void signInWithOAuth()}>
                <UserPlus className="h-3.5 w-3.5 mr-1.5" />
                Sign in to Matrx
              </Button>
            ) : null}
            <Button
              size="sm"
              variant="outline"
              onClick={() => navigate("/settings")}
            >
              <KeyRound className="h-3.5 w-3.5 mr-1.5" />
              Open Settings (account and API keys)
            </Button>
          </>
        )}
        {kind === "engine" ? (
          <Button
            size="sm"
            variant="outline"
            onClick={() => navigate("/activity")}
          >
            View Activity / logs
          </Button>
        ) : null}
      </div>
    </div>
  );
}

/** Simple retryable status-error card (video surface). */
export function StatusErrorCard({
  title,
  message,
  onRetry,
}: {
  title: string;
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-4 space-y-3">
      <div className="flex items-start gap-3">
        <AlertCircle className="h-4 w-4 text-destructive mt-0.5 shrink-0" />
        <div className="text-sm space-y-1.5 min-w-0">
          <p className="font-medium text-foreground">{title}</p>
          <p className="text-muted-foreground text-xs leading-relaxed break-all">
            {message}
          </p>
        </div>
      </div>
      <Button size="sm" variant="secondary" onClick={onRetry}>
        <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
        Try again
      </Button>
    </div>
  );
}

/** Amber "update AI packages" banner shown while packages are outdated. */
export function OutdatedPackagesBanner({
  extra,
}: {
  /** Optional trailing content (e.g. an "Open Models" button). */
  extra?: ReactNode;
}) {
  const [state] = useMediaGenApp();
  const { imageStatus } = state;
  if (imageStatus?.packages_outdated !== true) return null;
  return (
    <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 px-4 py-3 flex items-start gap-3">
      <PackagePlus className="h-4 w-4 text-amber-500 mt-0.5 shrink-0" />
      <div className="min-w-0 flex-1 space-y-2">
        <div>
          <p className="text-sm font-medium">Update AI packages</p>
          <p className="text-xs text-muted-foreground leading-relaxed">
            Your on-device AI packages
            {imageStatus.packages_version
              ? ` (diffusers ${imageStatus.packages_version})`
              : ""}{" "}
            are older than required for the latest image and video models.
            Update to unlock the new model catalog.
          </p>
        </div>
        {extra}
      </div>
    </div>
  );
}

/**
 * Image readiness gate: spinner → classified error card → installer →
 * children. Does NOT handle packages_outdated (that is a banner + Models-view
 * installer, composed by the caller via OutdatedPackagesBanner).
 */
export function ImageGenGate({ children }: { children: ReactNode }) {
  const [state, actions] = useMediaGenApp();
  const { imageStatus, imageModels, imageStatusLoading, imageStatusError } =
    state;
  const { refreshImage } = actions;

  if (imageStatusLoading && !imageStatus) {
    return <CenteredSpinner text="Checking image generation status…" />;
  }
  if (imageStatusError) {
    return (
      <ImageStatusErrorCard
        error={imageStatusError}
        onRetry={() => void refreshImage()}
      />
    );
  }
  if (imageStatus && !imageStatus.available) {
    return (
      <ImageGenInstaller
        models={imageModels}
        onInstallComplete={() => void refreshImage()}
      />
    );
  }
  return <>{children}</>;
}

/**
 * Video readiness gate: spinner → error card → hardware gate → shared-package
 * installer → children.
 */
export function VideoGenGate({ children }: { children: ReactNode }) {
  const [state, actions] = useMediaGenApp();
  const { videoStatus, videoModels, videoStatusLoading, videoStatusError } =
    state;
  const { refreshVideo } = actions;

  if (videoStatusLoading && !videoStatus) {
    return <CenteredSpinner text="Checking video generation status…" />;
  }
  if (videoStatusError) {
    return (
      <StatusErrorCard
        title="Could not load video generation"
        message={videoStatusError}
        onRetry={() => void refreshVideo()}
      />
    );
  }
  if (videoStatus && !videoStatus.hardware_supported) {
    return (
      <div className="rounded-xl border px-5 py-8 flex flex-col items-center text-center gap-3">
        <div className="rounded-lg bg-muted p-3">
          <MonitorX className="h-6 w-6 text-muted-foreground" />
        </div>
        <p className="font-semibold text-sm">
          Video generation is not available on this computer
        </p>
        <p className="text-xs text-muted-foreground leading-relaxed max-w-md">
          {videoStatus.hardware_reason ??
            videoStatus.unavailable_reason ??
            "Video generation requires Apple Silicon with 16GB+ memory or an NVIDIA GPU with 8GB+ VRAM."}
        </p>
        <p className="text-[11px] text-muted-foreground">
          Image generation may still work — check the Images tab.
        </p>
      </div>
    );
  }
  if (videoStatus && !videoStatus.packages_installed) {
    return (
      <ImageGenInstaller
        models={videoModels}
        headline="Set up Video Generation"
        intro="AI Matrx can generate short videos directly on your computer. Video uses the same on-device AI packages as image generation — click Install now for the one-time setup, then download a video model."
        onInstallComplete={() => void refreshVideo()}
      />
    );
  }
  return <>{children}</>;
}
