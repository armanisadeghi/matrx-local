/**
 * TranscriptionMiniMode — compact floating overlay for live transcription.
 *
 * A small card that starts in the bottom-right corner. It moves through
 * summon's Dock (drag by the header bar, clamped to the window, position
 * remembered) and resizes with the native corner grip. All transcription
 * controls are intact in this mode.
 */

import { useState } from "react";
import { X, Maximize2, GripVertical } from "lucide-react";
import { Dock, DockHandle } from "@ai-matrx/summon/dock";
import { RecordingMicButton } from "@/components/recording/RecordingMicButton";
import { RmsLevelBar } from "@/components/recording/RmsLevelBar";

interface MiniModeProps {
  isRecording: boolean;
  isProcessingTail: boolean;
  isCalibrating: boolean;
  liveRms: number;
  recentText: string;
  onStartRecording: () => Promise<void>;
  onStopRecording: () => Promise<void>;
  onExpand: () => void;
  onClose: () => void;
}

const MIN_WIDTH = 280;
const MIN_HEIGHT = 120;
const DEFAULT_WIDTH = 340;
const DEFAULT_HEIGHT = 160;
const EDGE_GAP = 24;

/** Bottom-right resting place, measured from the top-left so the corner grip grows the card toward the pointer. */
function bottomRightOffset() {
  if (typeof window === "undefined") return { x: EDGE_GAP, y: EDGE_GAP };
  return {
    x: Math.max(EDGE_GAP, window.innerWidth - DEFAULT_WIDTH - EDGE_GAP),
    y: Math.max(EDGE_GAP, window.innerHeight - DEFAULT_HEIGHT - EDGE_GAP),
  };
}

export function TranscriptionMiniMode({
  isRecording,
  isProcessingTail,
  isCalibrating,
  liveRms,
  recentText,
  onStartRecording,
  onStopRecording,
  onExpand,
  onClose,
}: MiniModeProps) {
  const [restingOffset] = useState(bottomRightOffset);

  // Keep only the last ~3 lines of text visible
  const displayText = recentText.length > 200 ? "…" + recentText.slice(-200) : recentText;

  return (
    <Dock
      id="matrx-local.transcription-mini-mode"
      corner="top-left"
      defaultPosition={restingOffset}
      zIndex={50}
      className="flex flex-col overflow-hidden rounded-2xl border border-border/80 bg-card/95 shadow-2xl backdrop-blur-sm select-none"
      style={{
        width: DEFAULT_WIDTH,
        height: DEFAULT_HEIGHT,
        minWidth: MIN_WIDTH,
        minHeight: MIN_HEIGHT,
        resize: "both",
      }}
    >
      {/* Drag handle bar */}
      <DockHandle className="flex shrink-0 items-center justify-between px-3 py-2 active:cursor-grabbing rounded-t-2xl border-b border-border/50">
        <div className="flex items-center gap-2">
          <GripVertical className="h-3.5 w-3.5 text-muted-foreground/50" />
          <span className="text-xs font-medium text-muted-foreground">
            {isRecording
              ? "Recording…"
              : isProcessingTail
              ? "Processing…"
              : "Transcription"}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={onExpand}
            className="flex h-5 w-5 items-center justify-center rounded hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
            title="Expand"
          >
            <Maximize2 className="h-3 w-3" />
          </button>
          <button
            onClick={onClose}
            className="flex h-5 w-5 items-center justify-center rounded hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
            title="Close mini mode"
          >
            <X className="h-3 w-3" />
          </button>
        </div>
      </DockHandle>

      {/* Content area */}
      <div className="flex min-h-0 flex-1 flex-col px-3 py-2 gap-2">
        {/* Text preview */}
        <div className="flex-1 overflow-hidden">
          {displayText ? (
            <p className="text-xs text-foreground/90 leading-relaxed line-clamp-3 break-words">
              {displayText}
            </p>
          ) : (
            <p className="text-xs text-muted-foreground/60 italic">
              {isRecording
                ? isCalibrating
                  ? "Calibrating mic…"
                  : "Listening…"
                : "Click the mic to start"}
            </p>
          )}
        </div>

        {/* Controls row */}
        <div className="flex items-center gap-2 shrink-0">
          <RecordingMicButton
            isRecording={isRecording}
            isProcessingTail={isProcessingTail}
            liveRms={liveRms}
            onToggle={isRecording ? onStopRecording : onStartRecording}
            size="xs"
          />

          {isRecording && (
            <div className="flex-1">
              <RmsLevelBar liveRms={liveRms} height="sm" showDot />
            </div>
          )}
          {!isRecording && !isProcessingTail && (
            <p className="text-xs text-muted-foreground">
              {recentText.length > 0
                ? `${recentText.length} chars`
                : "Ready"}
            </p>
          )}
          {isProcessingTail && (
            <p className="text-xs text-amber-500">Finishing…</p>
          )}
        </div>
      </div>
    </Dock>
  );
}
