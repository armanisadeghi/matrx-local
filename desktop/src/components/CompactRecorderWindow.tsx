/**
 * CompactRecorderWindow — fills the entire shrunken (420 × 260) OS window.
 *
 * Decorations are OFF in this mode, so we draw our own title bar and use
 * Tauri's `data-tauri-drag-region` attribute to make the whole header area
 * draggable. This is the only reliable way to drag a decoration-less window
 * on macOS — `startDragging()` requires a real mousedown target with no
 * preventDefault, whereas data-tauri-drag-region is handled natively by Tauri.
 *
 * Recording starts automatically when this component mounts.
 */

import { copyText } from "@ai-matrx/kit/clipboard";
import { useEffect, useRef, useCallback, useState } from "react";
import {
  Loader2,
  Maximize2,
  Copy,
  Check,
  MicOff,
} from "lucide-react";
import { RecordingMicButton } from "@/components/recording/RecordingMicButton";
import { RmsLevelBar } from "@/components/recording/RmsLevelBar";
import { cn } from "@/lib/utils";

interface CompactRecorderWindowProps {
  isRecording: boolean;
  isProcessingTail: boolean;
  isCalibrating: boolean;
  liveRms: number;
  /** Full accumulated transcript text for the current session. */
  transcript: string;
  onStartRecording: () => Promise<void>;
  onStopRecording: () => Promise<void>;
  /** Called when the user clicks the expand icon — exits compact mode. */
  onExpand: () => void;
}

export function CompactRecorderWindow({
  isRecording,
  isProcessingTail,
  isCalibrating,
  liveRms,
  transcript,
  onStartRecording,
  onStopRecording,
  onExpand,
}: CompactRecorderWindowProps) {
  const [copied, setCopied] = useState(false);
  const textRef = useRef<HTMLDivElement>(null);
  const didAutoStartRef = useRef(false);

  // Auto-start recording the moment this window appears.
  // Guard with a ref so StrictMode double-mount doesn't start twice.
  useEffect(() => {
    if (didAutoStartRef.current) return;
    didAutoStartRef.current = true;
    if (!isRecording && !isProcessingTail) {
      onStartRecording().catch(() => {
        // If auto-start fails (e.g. mic not set up), the user can tap manually.
      });
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-scroll transcript to bottom as new words arrive.
  useEffect(() => {
    if (textRef.current) {
      textRef.current.scrollTop = textRef.current.scrollHeight;
    }
  }, [transcript]);

  const handleCopy = useCallback(() => {
    if (!transcript) return;
    void copyText(transcript).then((landed) => {
      if (!landed) return;
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }, [transcript]);

  const displayText =
    transcript.length > 400 ? "…" + transcript.slice(-400) : transcript;

  const statusLabel = isRecording
    ? isCalibrating
      ? "Calibrating…"
      : "Recording"
    : isProcessingTail
    ? "Processing…"
    : "AI Matrx · Recorder";

  return (
    <div className="flex flex-col h-screen w-screen bg-card text-foreground select-none overflow-hidden rounded-xl border border-border/60 shadow-2xl">

      {/* ── Title bar — drag region ───────────────────────────────────────── */}
      {/*
        data-tauri-drag-region tells Tauri to initiate an OS-level window drag
        on mousedown anywhere inside this element. This is the only approach
        that works reliably on macOS when window decorations are disabled.
        Do NOT attach onMouseDown/preventDefault here — it breaks the drag.
      */}
      <div
        data-tauri-drag-region
        className="flex items-center justify-between px-3 py-2 border-b border-border/50 shrink-0 cursor-grab"
      >
        <div
          data-tauri-drag-region
          className="flex items-center gap-2 min-w-0 flex-1"
        >
          {/* Recording dot indicator */}
          {isRecording && (
            <div className="h-2 w-2 rounded-full bg-red-500 animate-pulse shrink-0" />
          )}
          {isProcessingTail && (
            <Loader2 className="h-3 w-3 text-amber-500 animate-spin shrink-0" />
          )}
          <span
            data-tauri-drag-region
            className={cn(
              "text-[11px] font-medium leading-none truncate",
              isRecording
                ? "text-red-400"
                : isProcessingTail
                ? "text-amber-400"
                : "text-muted-foreground"
            )}
          >
            {statusLabel}
          </span>
        </div>

        {/* Buttons — NOT part of drag region (clicks must work normally) */}
        <div className="flex items-center gap-1 shrink-0 ml-2">
          {transcript.length > 0 && (
            <button
              onClick={handleCopy}
              className="flex h-5 w-5 items-center justify-center rounded text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
              title="Copy transcript"
            >
              {copied ? (
                <Check className="h-3 w-3 text-green-500" />
              ) : (
                <Copy className="h-3 w-3" />
              )}
            </button>
          )}
          <button
            onClick={onExpand}
            className="flex h-5 w-5 items-center justify-center rounded text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
            title="Expand to full app"
          >
            <Maximize2 className="h-3 w-3" />
          </button>
        </div>
      </div>

      {/* ── Transcript area ───────────────────────────────────────────────── */}
      <div
        ref={textRef}
        className="flex-1 overflow-y-auto px-3 py-2 min-h-0"
      >
        {displayText ? (
          <p className="text-[12px] leading-relaxed text-foreground/90 break-words whitespace-pre-wrap">
            {displayText}
          </p>
        ) : (
          <p className="text-[12px] text-muted-foreground/50 italic">
            {isRecording
              ? isCalibrating
                ? "Calibrating microphone level…"
                : "Listening — speak now"
              : isProcessingTail
              ? "Processing final audio…"
              : "Tap the mic to start"}
          </p>
        )}
      </div>

      {/* ── Controls bar ─────────────────────────────────────────────────── */}
      <div className="flex items-center gap-2.5 px-3 py-2.5 border-t border-border/50 shrink-0">
        <RecordingMicButton
          isRecording={isRecording}
          isProcessingTail={isProcessingTail}
          liveRms={liveRms}
          onToggle={isRecording ? onStopRecording : onStartRecording}
          size="sm"
          stopIcon="square"
        />

        {/* RMS meter / status text */}
        <div className="flex-1 flex flex-col gap-1 min-w-0">
          {isRecording ? (
            <RmsLevelBar
              liveRms={liveRms}
              height="sm"
              showDot
              showReadout
              label="REC"
            />
          ) : isProcessingTail ? (
            <span className="text-[11px] text-amber-400">Finishing…</span>
          ) : (
            <span className="text-[11px] text-muted-foreground">
              {transcript.length > 0
                ? `${transcript.length} chars · tap mic to record more`
                : "Ready to record"}
            </span>
          )}
        </div>

        {isRecording && (
          <span title="Tap button to stop">
            <MicOff className="h-3.5 w-3.5 text-red-400/60 shrink-0" />
          </span>
        )}
      </div>
    </div>
  );
}
