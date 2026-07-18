/**
 * Panel manifest — the single source of truth for lightweight panel windows.
 *
 * Each entry declares exactly which context providers a panel needs (verified
 * by transitive context-hook scan of the page's component tree — do NOT add
 * providers "just in case", and DO add one here when a page gains a new
 * context dependency, or the panel window will crash with a
 * "must be used inside <Provider>" error).
 *
 * Keep page ids in sync with:
 *   • `PANEL_PAGES` in desktop/src-tauri/src/windows.rs (labels, sizes, titles)
 *   • `PanelPage` in desktop/src/lib/window-role.ts
 */

import type { ComponentType, ReactNode } from "react";
import type { PanelPage } from "@/lib/window-role";

import { AccessHealthProvider } from "@/contexts/AccessHealthContext";
import { DownloadManagerProvider } from "@/contexts/DownloadManagerContext";
import { DownloadManagerModal } from "@/components/downloads/DownloadManagerModal";
import { MediaGenProvider } from "@/contexts/MediaGenContext";
import { PromptMatrixProvider } from "@/contexts/PromptMatrixContext";
import { MediaLibraryProvider } from "@/contexts/MediaLibraryContext";
import { MediaVaultProvider } from "@/contexts/MediaVaultContext";
import { MediaActionsProvider } from "@/components/media/MediaActionsProvider";
import { TtsProvider } from "@/contexts/TtsContext";
import { LlmProvider } from "@/contexts/LlmContext";
import { WakeWordProvider } from "@/contexts/WakeWordContext";
import { TranscriptionSessionsProvider } from "@/contexts/TranscriptionSessionsContext";
import { PermissionsProvider } from "@/contexts/PermissionsContext";
import { AudioDevicesProvider } from "@/contexts/AudioDevicesContext";
import { TranscriptionProvider } from "@/contexts/TranscriptionContext";

import { Chat } from "@/pages/Chat";
import { CloudChat } from "@/pages/CloudChat";
import { Documents } from "@/pages/Documents";
import { Activity } from "@/pages/Activity";
import { Ports } from "@/pages/Ports";
import { Voice } from "@/pages/Voice";
import { TextToSpeech } from "@/pages/TextToSpeech";
import { MediaGeneration } from "@/pages/MediaGeneration";
import { MediaGalleryPanel } from "@/panels/pages/MediaGalleryPanel";

import type { EngineStatus } from "@/hooks/use-engine";
import type { User } from "@supabase/supabase-js";

/** Engine/auth context handed to each panel's render function. */
export interface PanelRenderCtx {
  status: EngineStatus;
  url: string | null;
  tools: string[];
  user: User | null;
}

/**
 * A provider layer receives live engine/auth context when it is composed.
 * Most providers only need `children`; context-aware providers such as
 * AccessHealthProvider use the same render-time EngineStatus as the page.
 */
export interface PanelProvider {
  id: string;
  wrap: (children: ReactNode, ctx: PanelRenderCtx) => ReactNode;
}

function provide(
  id: string,
  Provider: ComponentType<{ children: ReactNode }>,
): PanelProvider {
  return {
    id,
    wrap: (children) => <Provider>{children}</Provider>,
  };
}

const ACCESS_HEALTH_PROVIDER: PanelProvider = {
  id: "access-health",
  wrap: (children, ctx) => (
    <AccessHealthProvider engineStatus={ctx.status}>{children}</AccessHealthProvider>
  ),
};

const DOWNLOAD_MANAGER_PROVIDER: PanelProvider = {
  id: "download-manager",
  wrap: (children) => (
    <DownloadManagerProvider>
      {children}
      <DownloadManagerModal />
    </DownloadManagerProvider>
  ),
};

const MEDIA_GEN_PROVIDER = provide("media-generation", MediaGenProvider);
const PROMPT_MATRIX_PROVIDER = provide("prompt-matrix", PromptMatrixProvider);
const MEDIA_LIBRARY_PROVIDER = provide("media-library", MediaLibraryProvider);
const MEDIA_VAULT_PROVIDER = provide("media-vault", MediaVaultProvider);
const MEDIA_ACTIONS_PROVIDER = provide("media-actions", MediaActionsProvider);
const TTS_PROVIDER = provide("tts", TtsProvider);
const LLM_PROVIDER = provide("llm", LlmProvider);
const WAKE_WORD_PROVIDER = provide("wake-word", WakeWordProvider);
const TRANSCRIPTION_SESSIONS_PROVIDER = provide(
  "transcription-sessions",
  TranscriptionSessionsProvider,
);
const PERMISSIONS_PROVIDER = provide("permissions", PermissionsProvider);
const AUDIO_DEVICES_PROVIDER = provide("audio-devices", AudioDevicesProvider);
const TRANSCRIPTION_PROVIDER = provide("transcription", TranscriptionProvider);

export interface PanelEntry {
  /** Window title (matches the Rust PANEL_PAGES table). */
  title: string;
  /**
   * Providers this panel mounts, OUTERMOST first. Order must respect the
   * dependency order used in App.tsx (e.g. PromptMatrix inside MediaGen;
   * MediaActions after Gen/Library/Vault; Transcription innermost).
   */
  providers: PanelProvider[];
  render: (ctx: PanelRenderCtx) => ReactNode;
}

export const PANEL_MANIFEST: Record<PanelPage, PanelEntry> = {
  chat: {
    title: "Confidential Chat",
    providers: [TTS_PROVIDER],
    render: ({ status, url, tools }) => (
      <Chat engineStatus={status} engineUrl={url} tools={tools} />
    ),
  },
  "cloud-chat": {
    title: "Cloud Chat",
    providers: [],
    render: ({ status, url }) => <CloudChat engineStatus={status} engineUrl={url} />,
  },
  notes: {
    title: "Notes",
    providers: [
      ACCESS_HEALTH_PROVIDER,
      PERMISSIONS_PROVIDER,
      TRANSCRIPTION_PROVIDER,
    ],
    render: ({ status, user }) => (
      <Documents engineStatus={status} userId={user?.id ?? null} />
    ),
  },
  activity: {
    title: "Activity",
    providers: [],
    render: ({ status, url }) => <Activity engineStatus={status} engineUrl={url} />,
  },
  ports: {
    title: "Ports",
    providers: [],
    render: ({ status, url }) => <Ports engineStatus={status} engineUrl={url} />,
  },
  transcription: {
    title: "Transcription",
    providers: [
      DOWNLOAD_MANAGER_PROVIDER,
      LLM_PROVIDER,
      WAKE_WORD_PROVIDER,
      TRANSCRIPTION_SESSIONS_PROVIDER,
      PERMISSIONS_PROVIDER,
      AUDIO_DEVICES_PROVIDER,
      TRANSCRIPTION_PROVIDER,
    ],
    render: () => <Voice />,
  },
  tts: {
    title: "Text to Speech",
    providers: [TTS_PROVIDER],
    render: () => <TextToSpeech />,
  },
  "media-generation": {
    title: "Media Generation",
    providers: [
      DOWNLOAD_MANAGER_PROVIDER,
      MEDIA_GEN_PROVIDER,
      PROMPT_MATRIX_PROVIDER,
      MEDIA_LIBRARY_PROVIDER,
      MEDIA_VAULT_PROVIDER,
      MEDIA_ACTIONS_PROVIDER,
    ],
    render: () => <MediaGeneration />,
  },
  "media-gallery": {
    title: "Media Gallery",
    providers: [
      DOWNLOAD_MANAGER_PROVIDER,
      MEDIA_GEN_PROVIDER,
      MEDIA_LIBRARY_PROVIDER,
      MEDIA_VAULT_PROVIDER,
      MEDIA_ACTIONS_PROVIDER,
    ],
    render: () => <MediaGalleryPanel />,
  },
};

/** Route → panel page mapping for "Move Page to New Window". */
export const ROUTE_TO_PANEL: Record<string, PanelPage> = {
  "/chat": "chat",
  "/cloud-chat": "cloud-chat",
  "/notes": "notes",
  "/activity": "activity",
  "/ports": "ports",
  "/voice": "transcription",
  "/tts": "tts",
  "/media-generation": "media-generation",
};
