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

import { DownloadManagerProvider } from "@/contexts/DownloadManagerContext";
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

type Provider = ComponentType<{ children: ReactNode }>;

export interface PanelEntry {
  /** Window title (matches the Rust PANEL_PAGES table). */
  title: string;
  /**
   * Providers this panel mounts, OUTERMOST first. Order must respect the
   * dependency order used in App.tsx (e.g. PromptMatrix inside MediaGen;
   * MediaActions after Gen/Library/Vault; Transcription innermost).
   */
  providers: Provider[];
  render: (ctx: PanelRenderCtx) => ReactNode;
}

export const PANEL_MANIFEST: Record<PanelPage, PanelEntry> = {
  chat: {
    title: "Confidential Chat",
    providers: [TtsProvider],
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
    providers: [PermissionsProvider, TranscriptionProvider],
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
      DownloadManagerProvider,
      LlmProvider,
      WakeWordProvider,
      TranscriptionSessionsProvider,
      PermissionsProvider,
      AudioDevicesProvider,
      TranscriptionProvider,
    ],
    render: () => <Voice />,
  },
  tts: {
    title: "Text to Speech",
    providers: [TtsProvider],
    render: () => <TextToSpeech />,
  },
  "media-generation": {
    title: "Media Generation",
    providers: [
      DownloadManagerProvider,
      MediaGenProvider,
      PromptMatrixProvider,
      MediaLibraryProvider,
      MediaVaultProvider,
      MediaActionsProvider,
    ],
    render: () => <MediaGeneration />,
  },
  "media-gallery": {
    title: "Media Gallery",
    providers: [
      DownloadManagerProvider,
      MediaGenProvider,
      MediaLibraryProvider,
      MediaVaultProvider,
      MediaActionsProvider,
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
