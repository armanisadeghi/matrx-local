/**
 * WakeWordOverlay
 *
 * A lightweight in-app ambient indicator that the wake word system is active.
 * It renders a subtle animated glow around the window border so users who are
 * already inside the AI Matrx window can tell at a glance that listening is live.
 *
 * This is NOT the primary user-facing feedback channel.  The primary feedback is:
 *   1. OS-level native notification (fires even when the app is in the background)
 *   2. Always-on-top floating transcript window (TranscriptOverlay component)
 *
 * This component only handles the in-app window-edge glow which is appropriate
 * regardless of which tab the user is on.  No full-screen flash, no transcript
 * text — all of that lives in the floating overlay window.
 *
 * States:
 *   active    — bright animated teal/blue border pulse
 *   dismissed — brief red flash, then nothing
 *   others    — nothing rendered
 */

import { useEffect, useRef } from "react";
import type { WakeWordUIMode } from "@/hooks/use-wake-word";
import { Button } from "@/components/ui/button";

// ── Keyframes injected once ───────────────────────────────────────────────────

const KEYFRAMES_ID = "ww-glow-kf";
function ensureKeyframes() {
  if (document.getElementById(KEYFRAMES_ID)) return;
  const s = document.createElement("style");
  s.id = KEYFRAMES_ID;
  s.textContent = `
    @keyframes ww-glow-breathe {
      0%, 100% { box-shadow: 0 0 0 2px hsl(var(--ring) / 0.55), 0 0 28px 6px hsl(var(--ring) / 0.18); }
      50%       { box-shadow: 0 0 0 3px hsl(var(--ring) / 0.85), 0 0 48px 10px hsl(var(--ring) / 0.30); }
    }
    @keyframes ww-glow-dismiss {
      0%   { box-shadow: 0 0 0 3px rgba(239,68,68,0.85), 0 0 40px 8px rgba(239,68,68,0.35); }
      100% { box-shadow: 0 0 0 0 rgba(239,68,68,0); }
    }
  `;
  document.head.appendChild(s);
}

// ── Component ─────────────────────────────────────────────────────────────────

interface WakeWordOverlayProps {
  uiMode: WakeWordUIMode;
  /** Unused — kept for API compatibility with existing callers */
  rms?: number;
  /** Unused — transcript lives in the floating overlay window */
  transcript?: string;
  onDismiss: () => void;
  /** Unused — publish lives in the floating overlay window */
  onPublishToNote?: (text: string) => Promise<void>;
}

export function WakeWordOverlay({ uiMode, onDismiss }: WakeWordOverlayProps) {
  const prevModeRef = useRef<WakeWordUIMode>("idle");

  useEffect(() => { ensureKeyframes(); }, []);

  prevModeRef.current = uiMode;

  // Only render in "active" or "dismissed" states
  if (uiMode !== "active" && uiMode !== "dismissed") return null;

  const isDismissed = uiMode === "dismissed";

  return (
    <>
      {/* Window-edge glow — fixed inset-0, pointer-events:none so it doesn't block clicks */}
      <div
        className="fixed inset-0 z-[50] pointer-events-none"
        style={{
          animation: isDismissed
            ? "ww-glow-dismiss 0.6s ease-out forwards"
            : "ww-glow-breathe 2s ease-in-out infinite",
        }}
      />
      {/* Minimal "Shut it up" button — always visible while active so user can dismiss from any tab */}
      {!isDismissed && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={onDismiss}
          className="fixed bottom-4 right-4 z-[51] rounded-full border-border/80 bg-background/85 text-foreground shadow-lg backdrop-blur-xl hover:border-destructive/50 hover:bg-destructive/10 hover:text-destructive"
          title="Stop listening"
        >
          ✕ Stop
        </Button>
      )}
    </>
  );
}
