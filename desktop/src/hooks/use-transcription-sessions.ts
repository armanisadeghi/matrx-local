/**
 * Hook for managing transcription session history.
 * Wraps the sessions persistence layer and exposes reactive state.
 */

import { useState, useCallback, useMemo, useEffect } from "react";
import type { TranscriptionSession, WhisperSegment } from "@/lib/transcription/types";
import {
  loadSessions,
  createSession,
  appendSegments,
  finalizeSession,
  renameSession,
  updateSessionText,
  polishSession,
  deleteSession,
  setFlushCallback,
  flushNow,
} from "@/lib/transcription/sessions";

export interface SessionsState {
  sessions: TranscriptionSession[];
  /** The session currently open in the editor (may differ from active recording) */
  viewingSessionId: string | null;
  viewingSession: TranscriptionSession | null;
}

export interface SessionsActions {
  /** Reload sessions from storage */
  refresh: () => void;
  /** Start a new session and return it */
  startNew: (modelUsed: string | null, deviceUsed: string | null) => TranscriptionSession;
  /** Append segments to an in-progress session */
  append: (sessionId: string, segments: WhisperSegment[]) => void;
  /** Mark a session done with its total duration */
  finalize: (sessionId: string, durationSecs: number) => void;
  /** Rename a session */
  rename: (sessionId: string, title: string | null) => void;
  /** Overwrite the full text of a session (user edits) */
  updateText: (sessionId: string, text: string) => void;
  /** Apply AI-polish results (polished text + metadata) to a session */
  applyPolish: (
    sessionId: string,
    opts: {
      polishedText: string;
      aiTitle: string | null;
      aiDescription: string | null;
      aiTags: string[];
    }
  ) => void;
  /** Delete a session */
  remove: (sessionId: string) => void;
  /** Open a session for viewing in the main panel */
  open: (sessionId: string | null) => void;
}

export function useTranscriptionSessions(): [SessionsState, SessionsActions] {
  const [sessions, setSessions] = useState<TranscriptionSession[]>(() =>
    loadSessions()
  );
  const [viewingSessionId, setViewingSessionId] = useState<string | null>(null);

  const refresh = useCallback(() => {
    setSessions(loadSessions());
  }, []);

  useEffect(() => {
    setFlushCallback(() => setSessions(loadSessions()));
    // Multi-window: another window's write lands here via the storage event
    // (writes are safe — every mutation in lib/transcription/sessions.ts
    // re-reads localStorage before merging — but this window's React state
    // would otherwise go stale until its own next mutation).
    const onStorage = (e: StorageEvent) => {
      if (e.key === "matrx-transcription-sessions") {
        setSessions(loadSessions());
      }
    };
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener("storage", onStorage);
      flushNow();
      setFlushCallback(null);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const startNew = useCallback(
    (modelUsed: string | null, deviceUsed: string | null): TranscriptionSession => {
      const session = createSession({ modelUsed, deviceUsed });
      setSessions(loadSessions());
      setViewingSessionId(session.id);
      return session;
    },
    []
  );

  const append = useCallback((sessionId: string, segments: WhisperSegment[]) => {
    appendSegments(sessionId, segments);
  }, []);

  const finalize = useCallback((sessionId: string, durationSecs: number) => {
    finalizeSession(sessionId, durationSecs);
    setSessions(loadSessions());
  }, []);

  const rename = useCallback((sessionId: string, title: string | null) => {
    renameSession(sessionId, title);
    setSessions(loadSessions());
  }, []);

  const updateText = useCallback((sessionId: string, text: string) => {
    updateSessionText(sessionId, text);
    setSessions(loadSessions());
  }, []);

  const applyPolish = useCallback(
    (
      sessionId: string,
      opts: {
        polishedText: string;
        aiTitle: string | null;
        aiDescription: string | null;
        aiTags: string[];
      }
    ) => {
      polishSession(sessionId, opts);
      setSessions(loadSessions());
    },
    []
  );

  const remove = useCallback(
    (sessionId: string) => {
      deleteSession(sessionId);
      const updated = loadSessions();
      setSessions(updated);
      if (viewingSessionId === sessionId) {
        setViewingSessionId(updated[0]?.id ?? null);
      }
    },
    [viewingSessionId]
  );

  const open = useCallback((sessionId: string | null) => {
    setViewingSessionId(sessionId);
  }, []);

  const viewingSession: TranscriptionSession | null = useMemo(
    () => (viewingSessionId ? (sessions.find((s) => s.id === viewingSessionId) ?? null) : null),
    [viewingSessionId, sessions],
  );

  const state: SessionsState = useMemo(
    () => ({ sessions, viewingSessionId, viewingSession }),
    [sessions, viewingSessionId, viewingSession],
  );

  const actions: SessionsActions = useMemo(
    () => ({ refresh, startNew, append, finalize, rename, updateText, applyPolish, remove, open }),
    [refresh, startNew, append, finalize, rename, updateText, applyPolish, remove, open],
  );

  return [state, actions];
}
