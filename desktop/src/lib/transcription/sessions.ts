/**
 * Transcription session persistence — localStorage-backed CRUD.
 *
 * Sessions are stored as a JSON array under STORAGE_KEY, sorted newest-first.
 * Audio files are currently kept in-memory only; future work can write them to
 * the filesystem via Tauri fs plugin.
 */

import type { TranscriptionSession, WhisperSegment } from "./types";

const STORAGE_KEY = "matrx-transcription-sessions";
const MAX_SESSIONS = 500;

const FLUSH_INTERVAL_MS = 1000;
const pendingSegments = new Map<string, WhisperSegment[]>();
let flushTimer: ReturnType<typeof setTimeout> | null = null;
let flushCallback: (() => void) | null = null;

function scheduleFlush(): void {
  if (flushTimer !== null) return;
  flushTimer = setTimeout(() => {
    flushTimer = null;
    flushPendingSegments();
  }, FLUSH_INTERVAL_MS);
}

function flushPendingSegments(): void {
  if (pendingSegments.size === 0) return;
  const sessions = loadSessions();
  let changed = false;

  for (const [sessionId, segs] of pendingSegments) {
    const idx = sessions.findIndex((s) => s.id === sessionId);
    if (idx === -1) continue;
    const session = sessions[idx];
    if (!session) continue;
    session.segments = [...session.segments, ...segs];
    session.fullText = session.segments
      .map((s) => s.text)
      .filter((t) => t.length > 0)
      .join(" ");
    session.charCount = session.fullText.length;
    session.updatedAt = new Date().toISOString();
    sessions[idx] = session;
    changed = true;
  }

  pendingSegments.clear();
  if (changed) {
    saveSessions(sessions);
    flushCallback?.();
  }
}

/** Register a callback that fires after each debounced flush to localStorage. */
export function setFlushCallback(cb: (() => void) | null): void {
  flushCallback = cb;
}

/** Force-flush any pending segments immediately. Call before finalize/unmount. */
export function flushNow(): void {
  if (flushTimer !== null) {
    clearTimeout(flushTimer);
    flushTimer = null;
  }
  flushPendingSegments();
}

function generateId(): string {
  return `ts_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
}

export function loadSessions(): TranscriptionSession[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as TranscriptionSession[];
      return Array.isArray(parsed) ? parsed : [];
    }
  } catch {
    // Corrupted storage
  }
  return [];
}

function saveSessions(sessions: TranscriptionSession[]): void {
  // Keep only the most recent MAX_SESSIONS to bound storage size
  const trimmed = sessions.slice(0, MAX_SESSIONS);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed));
}

/** Create a new empty session and persist it. Returns the new session. */
export function createSession(opts: {
  modelUsed: string | null;
  deviceUsed: string | null;
}): TranscriptionSession {
  const now = new Date().toISOString();
  const session: TranscriptionSession = {
    id: generateId(),
    title: null,
    createdAt: now,
    updatedAt: now,
    durationSecs: 0,
    charCount: 0,
    modelUsed: opts.modelUsed,
    deviceUsed: opts.deviceUsed,
    segments: [],
    fullText: "",
  };
  const sessions = loadSessions();
  sessions.unshift(session);
  saveSessions(sessions);
  return session;
}

/** Append new segments to an existing session (debounced write). */
export function appendSegments(
  sessionId: string,
  newSegments: WhisperSegment[],
): void {
  const existing = pendingSegments.get(sessionId) ?? [];
  pendingSegments.set(sessionId, [...existing, ...newSegments]);
  scheduleFlush();
}

/** Mark a session as finished with its final duration.
 *  Flushes any pending segment writes first to ensure nothing is lost. */
export function finalizeSession(
  sessionId: string,
  durationSecs: number,
): TranscriptionSession | null {
  flushNow();
  const sessions = loadSessions();
  const idx = sessions.findIndex((s) => s.id === sessionId);
  if (idx === -1) return null;
  const session = sessions[idx];
  if (!session) return null;

  session.durationSecs = durationSecs;
  session.updatedAt = new Date().toISOString();
  saveSessions(sessions);
  return session;
}

/** Update just the title of a session. */
export function renameSession(
  sessionId: string,
  title: string | null,
): TranscriptionSession | null {
  const sessions = loadSessions();
  const idx = sessions.findIndex((s) => s.id === sessionId);
  if (idx === -1) return null;
  const session = sessions[idx];
  if (!session) return null;

  session.title = title;
  session.updatedAt = new Date().toISOString();
  saveSessions(sessions);
  return session;
}

/** Overwrite the fullText of a session (user-edited content) and recount chars. */
export function updateSessionText(
  sessionId: string,
  newText: string,
): TranscriptionSession | null {
  const sessions = loadSessions();
  const idx = sessions.findIndex((s) => s.id === sessionId);
  if (idx === -1) return null;
  const session = sessions[idx];
  if (!session) return null;

  session.fullText = newText;
  session.charCount = newText.length;
  session.updatedAt = new Date().toISOString();
  saveSessions(sessions);
  return session;
}

/**
 * Apply AI-polish results to a session.
 *
 * - Saves the original rawText on first polish (preserves it on subsequent runs).
 * - Overwrites fullText with the polished text.
 * - Updates title, aiTitle, aiDescription, aiTags, aiProcessedAt.
 */
export function polishSession(
  sessionId: string,
  opts: {
    polishedText: string;
    aiTitle: string | null;
    aiDescription: string | null;
    aiTags: string[];
  },
): TranscriptionSession | null {
  const sessions = loadSessions();
  const idx = sessions.findIndex((s) => s.id === sessionId);
  if (idx === -1) return null;

  const session = sessions[idx];
  if (!session) return null;
  const now = new Date().toISOString();

  // Preserve the original raw transcript on the first AI-polish run only.
  if (!session.rawText) {
    session.rawText = session.fullText;
  }

  session.fullText = opts.polishedText;
  session.charCount = opts.polishedText.length;

  // Apply AI title — also update the display title if the user hasn't set one
  session.aiTitle = opts.aiTitle;
  if (opts.aiTitle && !session.title) {
    session.title = opts.aiTitle;
  }

  session.aiDescription = opts.aiDescription;
  session.aiTags = opts.aiTags;
  session.aiProcessedAt = now;
  session.updatedAt = now;

  sessions[idx] = session;
  saveSessions(sessions);
  return session;
}

/** Delete a session by ID. */
export function deleteSession(sessionId: string): void {
  const sessions = loadSessions();
  saveSessions(sessions.filter((s) => s.id !== sessionId));
}

/** Load a single session by ID. */
export function getSession(sessionId: string): TranscriptionSession | null {
  const sessions = loadSessions();
  return sessions.find((s) => s.id === sessionId) ?? null;
}
