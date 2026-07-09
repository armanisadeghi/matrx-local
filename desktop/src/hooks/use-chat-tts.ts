/**
 * use-chat-tts — bridges LLM streaming chat responses to TTS playback.
 *
 * Watches an actively streaming assistant message, buffers text at sentence
 * boundaries, converts each sentence from markdown to speech-friendly text,
 * and forwards each chunk to ``ttsActions.speakText()`` which owns all
 * playback (single source of truth).
 *
 * Critical design rule: this hook never plays audio itself. The Web Audio
 * scheduler in ``use-tts.ts`` is the only playback path, so two simultaneous
 * read-aloud attempts cannot collide.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { parseMarkdownToText } from "@/lib/parse-markdown-for-speech";
import { loadSettings } from "@/lib/settings";
import { logError } from "@/lib/error-reporting";
import type { UseTtsActions, TtsLastError } from "./use-tts";

interface ChatMessage {
  id: string;
  content: string;
  isStreaming?: boolean;
}

const SENTENCE_END = /[.!?;]\s*$/;
const MIN_CHUNK_LEN = 30;

export interface UseChatTtsReturn {
  isReadingAloud: boolean;
  isPaused: boolean;
  /**
   * Last read-aloud synthesis failure, or null. Set when a sentence chunk
   * fails to synthesize (e.g. TTS model not downloaded); cleared on the next
   * successful chunk or new read-aloud attempt. Consumed by the chat UI so the
   * failure is visible instead of silently logged.
   */
  readAloudError: TtsLastError | null;
  clearReadAloudError: () => void;
  startReadAloud: (messageContent?: string) => void;
  stopReadAloud: () => void;
  pauseReadAloud: () => void;
  resumeReadAloud: () => void;
  readCompleteMessage: (content: string) => void;
}

export function useChatTts(
  ttsActions: UseTtsActions | null,
  activeMessage: ChatMessage | null,
  llmIsStreaming: boolean,
): UseChatTtsReturn {
  const [isReadingAloud, setIsReadingAloud] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [readAloudError, setReadAloudError] = useState<TtsLastError | null>(
    null,
  );

  const clearReadAloudError = useCallback(() => setReadAloudError(null), []);

  const abortRef = useRef<AbortController | null>(null);
  const readAloudActiveRef = useRef(false);
  const sentIndexRef = useRef(0);
  const pendingBufferRef = useRef("");

  // Keep ttsActions in a ref so callbacks always see the current reference
  const ttsActionsRef = useRef(ttsActions);
  ttsActionsRef.current = ttsActions;

  // Read-aloud only runs when ttsActions is available and the AudioContext
  // path is functional. We don't keep a fallback queue — callers see an error
  // via ttsActions.lastError if synthesis fails.
  const chatVoiceRef = useRef<string>("");
  const chatSpeedRef = useRef<number>(0);

  useEffect(() => {
    loadSettings().then((s) => {
      chatVoiceRef.current = s.ttsChatVoice || s.ttsDefaultVoice;
      chatSpeedRef.current = s.ttsChatSpeed || s.ttsDefaultSpeed;
    });
    const onChanged = () => {
      loadSettings().then((s) => {
        chatVoiceRef.current = s.ttsChatVoice || s.ttsDefaultVoice;
        chatSpeedRef.current = s.ttsChatSpeed || s.ttsDefaultSpeed;
      });
    };
    window.addEventListener("matrx-settings-changed", onChanged);
    return () =>
      window.removeEventListener("matrx-settings-changed", onChanged);
  }, []);

  /**
   * Speak a single chunk via the shared TTS actions.
   *
   * The queue is kept unbreakable: a failed sentence never rejects, so one
   * bad chunk can't kill the rest of the read-aloud. But the failure is no
   * longer invisible — it's surfaced via ``readAloudError`` so the chat UI can
   * show it. A subsequent successful chunk clears the error.
   */
  const _speakChunk = useCallback(
    async (text: string, signal: AbortSignal) => {
      if (!text.trim() || signal.aborted) return;
      const speechText = parseMarkdownToText(text);
      if (!speechText.trim()) return;

      const actions = ttsActionsRef.current;
      if (!actions) return;

      try {
        const err = await actions.speakText(
          speechText,
          chatVoiceRef.current || undefined,
          chatSpeedRef.current || undefined,
          signal,
        );
        if (err) {
          setReadAloudError(err);
          logError(
            "chat-tts",
            "speak chunk",
            new Error(`${err.code}: ${err.message}`),
          );
        } else if (!signal.aborted) {
          // Clean playback — clear any prior failure.
          setReadAloudError(null);
        }
      } catch (e) {
        if ((e as Error).name !== "AbortError") {
          setReadAloudError({
            code: "client_error",
            message: (e as Error).message,
          });
          logError("chat-tts", "speak chunk", e);
        }
      }
    },
    [],
  );

  // Serialization queue for sentence chunks. Each chunk's _runStream in
  // use-tts begins with a hard stopAudio(), so dispatching chunks
  // concurrently cuts off the still-playing previous sentence. Chaining each
  // chunk onto the previous one's completion (speakText resolves at playback
  // drain) yields sequential, uncut playback.
  const speakQueueRef = useRef<Promise<void>>(Promise.resolve());

  const _enqueueChunk = useCallback(
    (text: string, signal: AbortSignal): Promise<void> => {
      const run = speakQueueRef.current.then(() => _speakChunk(text, signal));
      // _speakChunk swallows its own errors, but keep the chain unbreakable.
      speakQueueRef.current = run.catch(() => {});
      return run;
    },
    [_speakChunk],
  );

  // Watch the streaming message content; when a sentence terminator appears
  // and the buffer is long enough, dispatch the chunk for synthesis.
  useEffect(() => {
    if (!readAloudActiveRef.current || !activeMessage?.isStreaming) return;

    const content = activeMessage.content;
    const newText = content.slice(sentIndexRef.current);
    if (!newText) return;

    const buf = pendingBufferRef.current + newText;
    sentIndexRef.current = content.length;

    if (buf.length >= MIN_CHUNK_LEN && SENTENCE_END.test(buf)) {
      pendingBufferRef.current = "";
      const abort = abortRef.current;
      if (abort && !abort.signal.aborted) {
        void _enqueueChunk(buf, abort.signal);
      }
    } else {
      pendingBufferRef.current = buf;
    }
  }, [activeMessage?.content, activeMessage?.isStreaming, _enqueueChunk]);

  // When the LLM stream ends, flush whatever short tail remains so we never
  // drop the last few words.
  useEffect(() => {
    if (!readAloudActiveRef.current) return;
    if (llmIsStreaming) return;

    const remaining = pendingBufferRef.current;
    pendingBufferRef.current = "";

    const abort = abortRef.current;
    const finalize = () => {
      // Only reset if a newer read-aloud hasn't taken over in the meantime.
      if (abortRef.current !== abort) return;
      readAloudActiveRef.current = false;
      setIsReadingAloud(false);
      setIsPaused(false);
    };

    if (remaining.trim()) {
      if (abort && !abort.signal.aborted) {
        // Queue behind any in-flight chunks, then wait for the queue to
        // drain before flipping the UI back to "not reading".
        _enqueueChunk(remaining, abort.signal).finally(finalize);
        return;
      }
      finalize();
      return;
    }
    // No tail to speak — still wait for already-queued chunks to finish
    // playing before resetting the button state.
    speakQueueRef.current.finally(finalize);
  }, [llmIsStreaming, _enqueueChunk]);

  /** Hard stop — abort outstanding fetches and tear down the audio. */
  const stopReadAloud = useCallback(() => {
    readAloudActiveRef.current = false;

    abortRef.current?.abort();
    abortRef.current = null;

    // Reset the chunk queue — aborted chunks settle immediately, and new
    // read-alouds must not wait behind a stale chain.
    speakQueueRef.current = Promise.resolve();

    ttsActionsRef.current?.stopAudio();

    sentIndexRef.current = 0;
    pendingBufferRef.current = "";
    setIsReadingAloud(false);
    setIsPaused(false);
  }, []);

  const pauseReadAloud = useCallback(() => {
    ttsActionsRef.current?.pauseAudio();
    setIsPaused(true);
  }, []);

  const resumeReadAloud = useCallback(() => {
    ttsActionsRef.current?.resumeAudio();
    setIsPaused(false);
  }, []);

  const startReadAloud = useCallback(
    (messageContent?: string) => {
      stopReadAloud();
      setReadAloudError(null);

      const abort = new AbortController();
      abortRef.current = abort;
      readAloudActiveRef.current = true;
      setIsReadingAloud(true);
      setIsPaused(false);

      if (messageContent) {
        sentIndexRef.current = messageContent.length;
        _enqueueChunk(messageContent, abort.signal).finally(() => {
          if (abortRef.current !== abort) return;
          readAloudActiveRef.current = false;
          setIsReadingAloud(false);
          setIsPaused(false);
        });
      } else {
        sentIndexRef.current = activeMessage?.content.length ?? 0;
        pendingBufferRef.current = "";
      }
    },
    [activeMessage, stopReadAloud, _enqueueChunk],
  );

  const readCompleteMessage = useCallback(
    (content: string) => {
      if (!content.trim()) return;
      stopReadAloud();
      setReadAloudError(null);

      const abort = new AbortController();
      abortRef.current = abort;
      readAloudActiveRef.current = true;
      setIsReadingAloud(true);
      setIsPaused(false);

      const speechText = parseMarkdownToText(content);
      if (!speechText.trim()) {
        readAloudActiveRef.current = false;
        setIsReadingAloud(false);
        return;
      }

      const actions = ttsActionsRef.current;
      if (!actions) {
        readAloudActiveRef.current = false;
        setIsReadingAloud(false);
        return;
      }

      actions
        .speakText(
          speechText,
          chatVoiceRef.current || undefined,
          chatSpeedRef.current || undefined,
          abort.signal,
        )
        .then((err) => {
          // speakText resolves with a non-null error on synthesis failure.
          if (err) setReadAloudError(err);
        })
        .catch((e) => {
          if ((e as Error).name !== "AbortError") {
            setReadAloudError({
              code: "client_error",
              message: (e as Error).message,
            });
            logError("chat-tts", "readCompleteMessage", e);
          }
        })
        .finally(() => {
          // Reset the button state too — but only if a newer read-aloud
          // hasn't taken over while this one was playing.
          if (abortRef.current !== abort) return;
          readAloudActiveRef.current = false;
          setIsReadingAloud(false);
          setIsPaused(false);
        });
    },
    [stopReadAloud],
  );

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      ttsActionsRef.current?.stopAudio();
    };
  }, []);

  return {
    isReadingAloud,
    isPaused,
    readAloudError,
    clearReadAloudError,
    startReadAloud,
    stopReadAloud,
    pauseReadAloud,
    resumeReadAloud,
    readCompleteMessage,
  };
}
