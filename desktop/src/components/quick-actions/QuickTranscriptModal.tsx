import { useState, useCallback, useEffect, useRef } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import {
  Copy,
  FileText,
  Sparkles,
  Loader2,
  Check,
  ExternalLink,
  AlertCircle,
} from "lucide-react";
import { RecordingMicButton } from "@/components/recording/RecordingMicButton";
import { RmsLevelBar } from "@/components/recording/RmsLevelBar";
import { useSessionsContext } from "@/contexts/TranscriptionSessionsContext";
import { useLlmApp } from "@/contexts/LlmContext";
import { useLlmPipeline, parsePolishOutput } from "@/hooks/use-llm-pipeline";
import { engine } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { TranscriptPolishOutput } from "@/hooks/use-llm-pipeline";
import type {
  TranscriptionState,
  TranscriptionActions,
} from "@/hooks/use-transcription";

interface QuickTranscriptModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  transcriptionState: TranscriptionState;
  transcriptionActions: TranscriptionActions;
  onNavigateToVoice?: () => void;
  userId?: string | null;
}

export function QuickTranscriptModal({
  open,
  onOpenChange,
  transcriptionState,
  transcriptionActions,
  onNavigateToVoice,
  userId,
}: QuickTranscriptModalProps) {
  const { actions: sessionsActions } = useSessionsContext();
  const [llmState] = useLlmApp();
  const llmPort = llmState.serverStatus?.port ?? null;
  const llmRunning = llmState.serverStatus?.running ?? false;
  const { run: runPipeline, running: polishing } = useLlmPipeline(
    () => llmPort,
  );

  const [sessionId, setSessionId] = useState<string | null>(null);
  const prevSegCountRef = useRef(0);
  const recordingStartRef = useRef(0);
  const autoStartedRef = useRef(false);

  const [copiedId, setCopiedId] = useState(false);
  const [pushingNote, setPushingNote] = useState(false);
  const [pushSuccess, setPushSuccess] = useState(false);
  const [polishSuccess, setPolishSuccess] = useState(false);
  const [pushError, setPushError] = useState<string | null>(null);
  const [polishError, setPolishError] = useState<string | null>(null);

  const {
    isRecording,
    isProcessingTail,
    fullTranscript,
    liveRms,
    activeModel,
    selectedDevice,
    isCalibrating,
  } = transcriptionState;

  const canRecord = !!activeModel && !isProcessingTail;
  const hasText = fullTranscript.trim().length > 0;
  const doneRecording = !isRecording && !isProcessingTail && sessionId !== null;

  // Auto-start recording on open
  useEffect(() => {
    if (open && canRecord && !isRecording && !autoStartedRef.current) {
      autoStartedRef.current = true;
      const session = sessionsActions.startNew(activeModel, selectedDevice);
      setSessionId(session.id);
      prevSegCountRef.current = transcriptionState.segments.length;
      recordingStartRef.current = Date.now();
      transcriptionActions.startRecording().catch((e) => console.warn("[transcript] startRecording failed:", e));
    }
    if (!open) {
      autoStartedRef.current = false;
    }
  }, [
    open,
    canRecord,
    isRecording,
    activeModel,
    selectedDevice,
    sessionsActions,
    transcriptionActions,
    transcriptionState.segments.length,
  ]);

  // Append segments to session in real-time
  useEffect(() => {
    if (!sessionId) return;
    if (transcriptionState.segments.length > prevSegCountRef.current) {
      const newSegs = transcriptionState.segments.slice(
        prevSegCountRef.current,
      );
      prevSegCountRef.current = transcriptionState.segments.length;
      sessionsActions.append(sessionId, newSegs);
    }
  }, [sessionId, transcriptionState.segments, sessionsActions]);

  // Finalize session when recording fully stops
  useEffect(() => {
    if (
      sessionId &&
      !isRecording &&
      !isProcessingTail &&
      recordingStartRef.current > 0
    ) {
      const dur = Math.round((Date.now() - recordingStartRef.current) / 1000);
      if (dur > 0) {
        sessionsActions.finalize(sessionId, dur);
        recordingStartRef.current = 0;
      }
    }
  }, [sessionId, isRecording, isProcessingTail, sessionsActions]);

  const handleToggle = useCallback(async () => {
    if (isRecording) {
      await transcriptionActions.stopRecording();
    } else if (canRecord) {
      if (!sessionId) {
        const session = sessionsActions.startNew(activeModel, selectedDevice);
        setSessionId(session.id);
        prevSegCountRef.current = transcriptionState.segments.length;
      }
      recordingStartRef.current = Date.now();
      await transcriptionActions.startRecording();
    }
  }, [
    isRecording,
    canRecord,
    sessionId,
    activeModel,
    selectedDevice,
    sessionsActions,
    transcriptionActions,
    transcriptionState.segments.length,
  ]);

  const handleClose = useCallback(
    async (v: boolean) => {
      if (!v && isRecording) {
        await transcriptionActions.stopRecording();
      }
      if (!v) {
        setSessionId(null);
        prevSegCountRef.current = 0;
        setCopiedId(false);
        setPushSuccess(false);
        setPolishSuccess(false);
        setPushError(null);
        setPolishError(null);
      }
      onOpenChange(v);
    },
    [isRecording, transcriptionActions, onOpenChange],
  );

  const handleCopy = useCallback(() => {
    if (!hasText) return;
    navigator.clipboard.writeText(fullTranscript.trim()).catch(() => {});
    setCopiedId(true);
    setTimeout(() => setCopiedId(false), 2000);
  }, [hasText, fullTranscript]);

  const handlePushToNote = useCallback(async () => {
    if (!hasText || !sessionId) return;
    setPushError(null);
    if (!engine.engineUrl) {
      setPushError(
        "Engine not connected — cannot save the note. Try again in a moment.",
      );
      return;
    }
    setPushingNote(true);
    try {
      const label = `Voice Note — ${new Date().toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}`;
      await engine.createNote(userId ?? "local", {
        label,
        content: fullTranscript.trim(),
        folder_name: "Voice Notes",
      });
      setPushSuccess(true);
      setTimeout(() => setPushSuccess(false), 3000);
    } catch (e) {
      setPushError(
        `Failed to save note: ${e instanceof Error ? e.message : String(e)}`,
      );
    } finally {
      setPushingNote(false);
    }
  }, [hasText, sessionId, fullTranscript, userId]);

  const handlePolish = useCallback(async () => {
    if (!hasText || !sessionId || !llmRunning) return;
    setPolishError(null);
    try {
      const raw = await runPipeline<TranscriptPolishOutput>(
        "polish_transcript",
        { transcript: fullTranscript.trim() },
      );
      const result = parsePolishOutput(raw, "", fullTranscript.trim());
      sessionsActions.applyPolish(sessionId, {
        polishedText: result.cleaned,
        aiTitle: result.title || null,
        aiDescription: result.description || null,
        aiTags: result.tags,
      });
      setPolishSuccess(true);
      setTimeout(() => setPolishSuccess(false), 4000);
    } catch (e) {
      setPolishError(
        `AI polish failed: ${e instanceof Error ? e.message : String(e)}`,
      );
    }
  }, [
    hasText,
    sessionId,
    llmRunning,
    runPipeline,
    fullTranscript,
    sessionsActions,
  ]);

  const handleOpenInVoice = useCallback(() => {
    if (sessionId) {
      sessionsActions.open(sessionId);
    }
    onOpenChange(false);
    onNavigateToVoice?.();
  }, [sessionId, sessionsActions, onOpenChange, onNavigateToVoice]);

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Quick Transcription</DialogTitle>
        </DialogHeader>

        {/* Recording controls */}
        <div className="flex flex-col items-center gap-3">
          <RecordingMicButton
            isRecording={isRecording}
            isProcessingTail={isProcessingTail}
            liveRms={liveRms}
            onToggle={handleToggle}
            disabled={!canRecord}
            size="md"
          />
          {isRecording && (
            <RmsLevelBar
              liveRms={liveRms}
              height="sm"
              showDot
              label={isCalibrating ? "Calibrating mic level…" : "Recording"}
              detail={selectedDevice ?? undefined}
            />
          )}
          {isProcessingTail && (
            <p className="text-xs text-amber-500">
              Finishing transcription of remaining audio…
            </p>
          )}
          {!isRecording && !isProcessingTail && !hasText && !activeModel && (
            <p className="text-xs text-muted-foreground">
              No transcription model loaded. Set up in the Speech to Text tab
              first.
            </p>
          )}
          {!isRecording && !isProcessingTail && !hasText && activeModel && (
            <p className="text-xs text-muted-foreground">
              Starting microphone…
            </p>
          )}
        </div>

        {/* Transcript display */}
        <div className="max-h-48 min-h-[6rem] overflow-auto rounded-lg border bg-muted/30 p-3">
          {hasText ? (
            <p className="text-sm whitespace-pre-wrap">{fullTranscript}</p>
          ) : (
            <p className="text-sm text-muted-foreground italic">
              Your words will appear here as you speak…
            </p>
          )}
        </div>

        {/* Post-recording actions */}
        {(hasText || doneRecording) && (
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              className={cn("gap-1.5 text-xs", copiedId && "text-emerald-500")}
              onClick={handleCopy}
              disabled={!hasText}
            >
              {copiedId ? (
                <Check className="h-3 w-3" />
              ) : (
                <Copy className="h-3 w-3" />
              )}
              {copiedId ? "Copied!" : "Copy"}
            </Button>

            <Button
              variant="outline"
              size="sm"
              className={cn(
                "gap-1.5 text-xs",
                pushSuccess && "text-emerald-500",
              )}
              onClick={handlePushToNote}
              disabled={pushingNote || !hasText}
            >
              {pushingNote ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : pushSuccess ? (
                <Check className="h-3 w-3" />
              ) : (
                <FileText className="h-3 w-3" />
              )}
              {pushSuccess ? "Saved!" : "Save as Note"}
            </Button>

            {llmRunning && (
              <Button
                variant="outline"
                size="sm"
                className={cn(
                  "gap-1.5 text-xs",
                  polishSuccess && "text-emerald-500",
                )}
                onClick={handlePolish}
                disabled={polishing || !hasText}
              >
                {polishing ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : polishSuccess ? (
                  <Check className="h-3 w-3" />
                ) : (
                  <Sparkles className="h-3 w-3" />
                )}
                {polishSuccess ? "Polished!" : "AI Polish"}
              </Button>
            )}

            {doneRecording && (
              <Button
                variant="ghost"
                size="sm"
                className="gap-1.5 text-xs ml-auto"
                onClick={handleOpenInVoice}
              >
                <ExternalLink className="h-3 w-3" />
                Open in Speech to Text tab
              </Button>
            )}
          </div>
        )}

        {(pushError || polishError) && (
          <div className="flex flex-col gap-1.5">
            {pushError && (
              <div
                role="alert"
                className="flex items-start gap-2 rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive"
              >
                <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span className="flex-1">{pushError}</span>
                <button
                  className="shrink-0 text-destructive/70 underline hover:text-destructive"
                  onClick={() => setPushError(null)}
                >
                  Dismiss
                </button>
              </div>
            )}
            {polishError && (
              <div
                role="alert"
                className="flex items-start gap-2 rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive"
              >
                <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span className="flex-1">{polishError}</span>
                <button
                  className="shrink-0 text-destructive/70 underline hover:text-destructive"
                  onClick={() => setPolishError(null)}
                >
                  Dismiss
                </button>
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
