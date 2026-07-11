/**
 * WakeWordActivePopup
 *
 * Floating in-app panel that appears when the wake word fires.
 * Shows:
 *   - Live transcription of the user's speech
 *   - The assistant's streaming text response
 *   - Phase status + mic controls
 *   - Dismiss button
 *
 * Positioned fixed bottom-right so it's visible from any tab.
 * Animates in from the bottom and stays on top of all content.
 */

import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { Mic, X, Square, Loader2 } from "lucide-react";
import { RecordingMicButton } from "@/components/recording/RecordingMicButton";
import type {
  VoiceChatState,
  VoiceChatActions,
  VoiceChatPhase,
} from "@/hooks/use-voice-chat";
import type { TranscriptionState } from "@/hooks/use-transcription";
import type { TtsPlaybackState } from "@/hooks/use-tts";

interface WakeWordActivePopupProps {
  /** Whether the popup should be visible. Controlled by parent. */
  visible: boolean;
  voiceChatState: VoiceChatState;
  voiceChatActions: VoiceChatActions;
  transcriptionState: TranscriptionState;
  isGenerating: boolean;
  stopGeneration: () => void;
  ttsPlaybackState: TtsPlaybackState;
  /** The latest streaming or completed assistant message content. */
  assistantContent: string | null;
  /** Called when the user dismisses the popup (also deactivates voice chat). */
  onDismiss: () => void;
}

const PHASE_LABELS: Record<VoiceChatPhase, string> = {
  idle: "Listening for your voice…",
  recording: "Listening…",
  processing: "Processing…",
  transcribed: "Ready to send",
  generating: "Thinking…",
  speaking: "Speaking…",
};

const PHASE_COLORS: Record<VoiceChatPhase, string> = {
  idle: "text-muted-foreground",
  recording: "text-red-400",
  processing: "text-amber-400",
  transcribed: "text-blue-400",
  generating: "text-primary",
  speaking: "text-green-400",
};

export function WakeWordActivePopup({
  visible,
  voiceChatState,
  voiceChatActions,
  transcriptionState,
  isGenerating,
  stopGeneration,
  ttsPlaybackState,
  assistantContent,
  onDismiss,
}: WakeWordActivePopupProps) {
  const { phase, autoMode, pendingTranscript, sessionBaseTranscript } =
    voiceChatState;
  const transcriptEndRef = useRef<HTMLDivElement>(null);
  const responseEndRef = useRef<HTMLDivElement>(null);
  const [isEntering, setIsEntering] = useState(false);

  // Animate in when visible becomes true; reset when hidden so re-show animates again.
  useEffect(() => {
    if (!visible) {
      setIsEntering(false);
      return;
    }
    setIsEntering(true);
    const id = setTimeout(() => setIsEntering(false), 300);
    return () => clearTimeout(id);
  }, [visible]);

  // Auto-scroll transcript
  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [pendingTranscript, transcriptionState.fullTranscript]);

  // Auto-scroll response
  useEffect(() => {
    responseEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [assistantContent]);

  if (!visible) return null;

  const isRecording = transcriptionState.isRecording;
  const isProcessingTail = transcriptionState.isProcessingTail;
  const liveRms = transcriptionState.liveRms;
  const isSpeaking =
    ttsPlaybackState === "playing" || ttsPlaybackState === "synthesizing";

  // Session-delta transcript during recording, final pending after
  const liveTranscript =
    phase === "recording" || phase === "processing"
      ? transcriptionState.fullTranscript
          .slice(sessionBaseTranscript.length)
          .trim()
      : pendingTranscript;

  const canStopGeneration = phase === "generating" && isGenerating;
  const canStopSpeaking = phase === "speaking" && isSpeaking;

  return (
    <div
      className={cn(
        // Fixed bottom-right, above everything including WakeWordOverlay
        "fixed bottom-16 right-4 z-[60] w-80",
        "flex flex-col gap-0 rounded-2xl border shadow-2xl",
        "bg-background/95 backdrop-blur-xl",
        "border-primary/20",
        "transition-all duration-300",
        isEntering
          ? "opacity-0 translate-y-4 scale-95"
          : "opacity-100 translate-y-0 scale-100",
      )}
      style={{
        boxShadow:
          "0 0 0 1px rgba(99,179,237,0.15), 0 20px 60px rgba(0,0,0,0.5), 0 0 30px rgba(99,179,237,0.1)",
      }}
    >
      {/* ── Header ── */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-border/50">
        <div className="flex items-center gap-2">
          <div className="flex h-5 w-5 items-center justify-center rounded-full bg-primary/15">
            <Mic className="h-3 w-3 text-primary" />
          </div>
          <span className="text-xs font-semibold text-foreground">
            Voice Assistant
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className={cn("text-[11px] font-medium", PHASE_COLORS[phase])}>
            {PHASE_LABELS[phase]}
          </span>
          <button
            onClick={onDismiss}
            className="ml-1 flex h-5 w-5 items-center justify-center rounded-full text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors"
            title="Dismiss"
          >
            <X className="h-3 w-3" />
          </button>
        </div>
      </div>

      {/* ── User transcript ── */}
      {(liveTranscript || phase === "recording" || phase === "processing") && (
        <div className="px-3 py-2 border-b border-border/30">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground mb-1">
            You
          </p>
          <div className="max-h-20 overflow-y-auto">
            <p className="text-xs text-foreground leading-relaxed whitespace-pre-wrap break-words">
              {liveTranscript || (
                <span className="text-muted-foreground italic">
                  {phase === "recording" ? "Listening…" : "Processing…"}
                </span>
              )}
              {phase === "recording" && (
                <span className="ml-0.5 inline-block h-3 w-[2px] bg-primary animate-pulse align-middle" />
              )}
            </p>
            <div ref={transcriptEndRef} />
          </div>
        </div>
      )}

      {/* ── Assistant response ── */}
      {(assistantContent || phase === "generating" || phase === "speaking") && (
        <div className="px-3 py-2 border-b border-border/30">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground mb-1">
            Assistant
          </p>
          <div className="max-h-28 overflow-y-auto">
            {phase === "generating" && !assistantContent ? (
              <div className="flex items-center gap-1.5 text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" />
                <span className="text-xs italic">Generating response…</span>
              </div>
            ) : (
              <p className="text-xs text-foreground leading-relaxed whitespace-pre-wrap break-words">
                {assistantContent}
                {phase === "generating" && (
                  <span className="ml-0.5 inline-block h-3 w-[2px] bg-primary animate-pulse align-middle" />
                )}
                {phase === "speaking" && (
                  <span className="ml-1.5 inline-flex items-center gap-0.5">
                    <SpeakingDots />
                  </span>
                )}
              </p>
            )}
            <div ref={responseEndRef} />
          </div>
        </div>
      )}

      {/* ── Controls ── */}
      <div className="flex items-center justify-between px-3 py-2">
        {/* Mic */}
        <RecordingMicButton
          isRecording={isRecording}
          isProcessingTail={isProcessingTail}
          liveRms={liveRms}
          onToggle={voiceChatActions.toggleRecording}
          disabled={
            isGenerating || isSpeaking || !transcriptionState.activeModel
          }
          size="xs"
          stopIcon="square"
        />

        {/* Auto-mode indicator */}
        <button
          onClick={() => voiceChatActions.setAutoMode(!autoMode)}
          className={cn(
            "text-[10px] font-medium px-2 py-0.5 rounded-full border transition-colors",
            autoMode
              ? "bg-primary/15 text-primary border-primary/30"
              : "text-muted-foreground border-border/50 hover:text-foreground",
          )}
          title={autoMode ? "Auto mode on" : "Manual mode"}
        >
          {autoMode ? "Auto" : "Manual"}
        </button>

        <div className="flex items-center gap-1">
          {/* Stop generation */}
          {canStopGeneration && (
            <button
              onClick={stopGeneration}
              className="flex h-6 w-6 items-center justify-center rounded-full bg-destructive/15 text-destructive hover:bg-destructive/25 transition-colors"
              title="Stop generation"
            >
              <Square className="h-3 w-3 fill-current" />
            </button>
          )}

          {/* Stop speaking */}
          {canStopSpeaking && (
            <button
              onClick={voiceChatActions.stopSpeaking}
              className="flex h-6 w-6 items-center justify-center rounded-full bg-destructive/15 text-destructive hover:bg-destructive/25 transition-colors"
              title="Stop speaking"
            >
              <Square className="h-3 w-3 fill-current" />
            </button>
          )}

          {/* Manual send */}
          {!autoMode && phase === "transcribed" && pendingTranscript.trim() && (
            <button
              onClick={voiceChatActions.sendPendingTranscript}
              className="text-[10px] font-medium px-2.5 py-1 rounded-full bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
            >
              Send
            </button>
          )}
        </div>
      </div>

      {/* ── Model not ready warning ── */}
      {!transcriptionState.activeModel && (
        <div className="px-3 pb-2 text-[10px] text-amber-500 text-center">
          Transcription model not ready — go to Speech to Text tab to set up
        </div>
      )}
    </div>
  );
}

function SpeakingDots() {
  return (
    <span className="inline-flex items-center gap-[2px] align-middle">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="inline-block w-[3px] rounded-full bg-green-400"
          style={{
            height: "8px",
            animation: `speaking-dot 0.7s ease-in-out ${i * 0.2}s infinite alternate`,
          }}
        />
      ))}
      <style>{`
        @keyframes speaking-dot {
          from { transform: scaleY(0.3); opacity: 0.4; }
          to   { transform: scaleY(1);   opacity: 1; }
        }
      `}</style>
    </span>
  );
}
