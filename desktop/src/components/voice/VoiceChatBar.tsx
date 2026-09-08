/**
 * VoiceChatBar
 *
 * Collapsible panel rendered below the chat input area when voice chat mode
 * is active. Shows:
 *   - Live transcript preview
 *   - Mic button (start / stop recording)
 *   - Phase status label
 *   - Send button (manual mode) / auto-mode toggle
 *   - Stop button (TTS or generation)
 *   - Deactivate (✕) button to close the panel
 */

import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";
import { Button } from "@ai-matrx/design-system";
import { Mic, X, Send, Square, Loader2, Zap, ZapOff } from "lucide-react";
import { RecordingMicButton } from "@/components/recording/RecordingMicButton";
import type { VoiceChatState, VoiceChatActions } from "@/hooks/use-voice-chat";
import type { TranscriptionState } from "@/hooks/use-transcription";

interface VoiceChatBarProps {
  voiceChatState: VoiceChatState;
  voiceChatActions: VoiceChatActions;
  transcriptionState: TranscriptionState;
  isGenerating: boolean;
  /** Call to abort LLM generation mid-stream. */
  stopGeneration: () => void;
  /** Whether TTS is currently speaking. */
  isSpeaking: boolean;
  /**
   * The transcript base snapshotted at the start of the current recording
   * session. Used to compute the live session-delta display during recording.
   * If omitted the component falls back to showing the last 3 transcript lines.
   */
  sessionBaseTranscript?: string;
}

const PHASE_LABELS: Record<VoiceChatState["phase"], string> = {
  idle: "Tap mic to speak",
  recording: "Listening…",
  processing: "Processing…",
  transcribed: "Ready to send",
  generating: "Thinking…",
  speaking: "Speaking…",
};

export function VoiceChatBar({
  voiceChatState,
  voiceChatActions,
  transcriptionState,
  isGenerating,
  stopGeneration,
  isSpeaking,
  sessionBaseTranscript = "",
}: VoiceChatBarProps) {
  const { phase, isActive, autoMode, pendingTranscript } = voiceChatState;
  const transcriptEndRef = useRef<HTMLDivElement>(null);

  // Auto-scroll transcript preview
  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [pendingTranscript, transcriptionState.fullTranscript]);

  if (!isActive) return null;

  const isRecording = transcriptionState.isRecording;
  const isProcessingTail = transcriptionState.isProcessingTail;
  const liveRms = transcriptionState.liveRms;

  // Live transcript for preview: show only the session-delta during recording
  // so that text from previous turns doesn't bleed into this turn's display.
  const displayTranscript =
    phase === "recording" || phase === "processing"
      ? transcriptionState.fullTranscript
          .slice(sessionBaseTranscript.length)
          .trim()
      : pendingTranscript;

  const canSend =
    !autoMode &&
    phase === "transcribed" &&
    !!pendingTranscript.trim() &&
    !isGenerating;

  const canStopGeneration = phase === "generating" && isGenerating;
  const canStopSpeaking = phase === "speaking" && isSpeaking;

  return (
    <div
      className={cn(
        "border-t bg-background/95 backdrop-blur-sm",
        "transition-all duration-300 ease-in-out",
      )}
    >
      <div className="max-w-3xl mx-auto px-3 py-2 space-y-2">
        {/* Header row */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Mic className="h-3.5 w-3.5 text-primary" />
            <span className="text-xs font-semibold text-foreground">
              Voice Chat
            </span>
            <span className="text-xs text-muted-foreground">
              — {PHASE_LABELS[phase]}
            </span>
          </div>
          <div className="flex items-center gap-1">
            {/* Auto-mode toggle */}
            <button
              onClick={() => voiceChatActions.setAutoMode(!autoMode)}
              className={cn(
                "flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-medium transition-colors",
                autoMode
                  ? "bg-primary/15 text-primary border border-primary/30"
                  : "text-muted-foreground border border-border hover:text-foreground",
              )}
              title={
                autoMode
                  ? "Auto mode: silence auto-submits, TTS auto-restarts mic"
                  : "Manual mode: click Send to submit"
              }
            >
              {autoMode ? (
                <Zap className="h-2.5 w-2.5" />
              ) : (
                <ZapOff className="h-2.5 w-2.5" />
              )}
              {autoMode ? "Auto" : "Manual"}
            </button>

            {/* Close button */}
            <button
              onClick={voiceChatActions.deactivate}
              className="flex h-6 w-6 items-center justify-center rounded-full text-muted-foreground hover:text-destructive transition-colors"
              title="Close voice chat"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>

        {/* Main controls row */}
        <div className="flex items-center gap-3">
          {/* Mic button */}
          <RecordingMicButton
            isRecording={isRecording}
            isProcessingTail={isProcessingTail}
            liveRms={liveRms}
            onToggle={voiceChatActions.toggleRecording}
            disabled={
              phase === "generating" ||
              phase === "speaking" ||
              !transcriptionState.activeModel
            }
            size="xs"
            stopIcon="square"
          />

          {/* Transcript preview */}
          <div className="flex-1 min-w-0 rounded-lg bg-muted/40 border border-border/50 px-3 py-1.5 min-h-[2rem] max-h-[5rem] overflow-y-auto">
            {displayTranscript ? (
              <p className="text-xs text-foreground leading-relaxed whitespace-pre-wrap break-words">
                {displayTranscript}
                {phase === "recording" && (
                  <span className="ml-0.5 inline-block h-3 w-[2px] bg-primary animate-pulse" />
                )}
              </p>
            ) : (
              <p className="text-xs text-muted-foreground italic">
                {!transcriptionState.activeModel
                  ? "Transcription model not ready"
                  : phase === "idle"
                    ? autoMode
                      ? "Tap mic to start — silence will auto-send"
                      : "Tap mic and speak, then click Send"
                    : ""}
              </p>
            )}
            <div ref={transcriptEndRef} />
          </div>

          {/* Action buttons */}
          <div className="flex items-center gap-1.5 shrink-0">
            {/* Processing spinner */}
            {phase === "processing" && (
              <div className="flex h-7 w-7 items-center justify-center">
                <Loader2 className="h-4 w-4 animate-spin text-amber-500" />
              </div>
            )}

            {/* Generating spinner */}
            {phase === "generating" && (
              <div className="flex h-7 w-7 items-center justify-center">
                <Loader2 className="h-4 w-4 animate-spin text-primary" />
              </div>
            )}

            {/* Speaking animation */}
            {phase === "speaking" && <SpeakingPulse />}

            {/* Send (manual mode) */}
            {!autoMode && (
              <Button
                size="sm"
                onClick={voiceChatActions.sendPendingTranscript}
                disabled={!canSend}
                className="h-7 px-2.5 text-xs gap-1"
                title="Send transcript to model"
              >
                <Send className="h-3 w-3" />
                Send
              </Button>
            )}

            {/* Stop generation */}
            {canStopGeneration && (
              <button
                onClick={stopGeneration}
                className="flex h-7 w-7 items-center justify-center rounded-full bg-destructive/15 text-destructive hover:bg-destructive/25 transition-colors"
                title="Stop generation"
              >
                <Square className="h-3 w-3 fill-current" />
              </button>
            )}

            {/* Stop speaking */}
            {canStopSpeaking && (
              <button
                onClick={voiceChatActions.stopSpeaking}
                className="flex h-7 w-7 items-center justify-center rounded-full bg-destructive/15 text-destructive hover:bg-destructive/25 transition-colors"
                title="Stop speaking"
              >
                <Square className="h-3 w-3 fill-current" />
              </button>
            )}

            {/* Clear transcript */}
            {phase === "transcribed" && !autoMode && (
              <button
                onClick={voiceChatActions.clearTranscript}
                className="flex h-7 w-7 items-center justify-center rounded-full text-muted-foreground hover:text-foreground transition-colors"
                title="Clear transcript"
              >
                <X className="h-3 w-3" />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/** Small animated waveform shown during TTS speaking phase. */
function SpeakingPulse() {
  return (
    <div className="flex items-center gap-[2px] h-7 px-1.5" title="Speaking…">
      {[0, 1, 2, 3].map((i) => (
        <span
          key={i}
          className="block w-[3px] rounded-full bg-primary"
          style={{
            height: `${10 + (i % 2) * 8}px`,
            animation: `speaking-bar 0.8s ease-in-out ${i * 0.15}s infinite alternate`,
          }}
        />
      ))}
      <style>{`
        @keyframes speaking-bar {
          from { transform: scaleY(0.4); opacity: 0.5; }
          to   { transform: scaleY(1);   opacity: 1; }
        }
      `}</style>
    </div>
  );
}
