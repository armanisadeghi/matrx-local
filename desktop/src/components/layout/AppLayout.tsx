import { useLocation } from "react-router-dom";
import { AppSidebar } from "./AppSidebar";
import { StatusBar } from "./StatusBar";
import { QuickActionBar } from "./QuickActionBar";
import { AppActionBanner } from "./AppActionBanner";
import { EngineDownBanner } from "@/components/EngineDownBanner";
import { useDevTerminalHeight } from "@/components/DevTerminalPanel";
import type { EngineStatus } from "@/hooks/use-engine";
import type { TranscriptionState, TranscriptionActions } from "@/hooks/use-transcription";
import type { AutoUpdateState, AutoUpdateActions } from "@/hooks/use-auto-update";
import type { AppNotification } from "@/hooks/use-notifications";
import type { User } from "@supabase/supabase-js";

const NOOP = () => {};

export interface PageEntry {
  /** The hash path this page owns, e.g. "/" or "/voice" */
  path: string;
  /** The fully constructed React element to keep alive */
  element: React.ReactNode;
}

interface AppLayoutProps {
  engineStatus: EngineStatus;
  engineUrl: string | null;
  engineVersion?: string;
  onRefresh: () => void;
  onOpenMonitor?: () => void;
  /** Full engine restart (stop → start → reconnect) — powers the down-banner. */
  onRestartEngine: () => Promise<void> | void;
  user: User | null;
  onSignOut: () => void;
  // QuickActionBar props
  isRecording: boolean;
  onRecord: () => void;
  onBackgroundRecord: () => void;
  isBackgroundRecording: boolean;
  transcriptionState: TranscriptionState;
  transcriptionActions: TranscriptionActions;
  tools: string[];
  updateState: AutoUpdateState;
  updateActions: AutoUpdateActions;
  // Notifications
  notifications: AppNotification[];
  unreadCount: number;
  onMarkRead: (id: string) => void;
  onMarkAllRead: () => void;
  onDismissNotification: (id: string) => void;
  onClearAllNotifications: () => void;
  /** All app pages — rendered permanently, shown/hidden by route */
  pages: PageEntry[];
}

/**
 * Returns true when the current pathname matches a page's registered path.
 * Exact match only — every navigable path (including sub-pages like
 * "/browser/tauri") is registered as its own PageEntry, so prefix matching
 * would render parent and child pages stacked on top of each other.
 */
function pageIsActive(pagePath: string, currentPathname: string): boolean {
  return currentPathname === pagePath;
}

export function AppLayout({
  engineStatus,
  engineUrl,
  engineVersion,
  onRefresh,
  onOpenMonitor,
  onRestartEngine,
  user,
  onSignOut,
  isRecording,
  onRecord,
  onBackgroundRecord,
  isBackgroundRecording,
  transcriptionState,
  transcriptionActions,
  tools,
  updateState,
  updateActions,
  notifications,
  unreadCount,
  onMarkRead,
  onMarkAllRead,
  onDismissNotification,
  onClearAllNotifications,
  pages,
}: AppLayoutProps) {
  const location = useLocation();
  const terminalHeight = useDevTerminalHeight();

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      <AppSidebar engineStatus={engineStatus} user={user} onSignOut={onSignOut} />
      <div
        className="flex flex-1 flex-col overflow-hidden transition-[padding-bottom] duration-150"
        style={{ paddingBottom: terminalHeight }}
      >
        <QuickActionBar
          isRecording={isRecording}
          onRecord={onRecord}
          onBackgroundRecord={onBackgroundRecord}
          isBackgroundRecording={isBackgroundRecording}
          engineStatus={engineStatus}
          engineUrl={engineUrl}
          tools={tools}
          onOpenMonitor={onOpenMonitor ?? NOOP}
          transcriptionState={transcriptionState}
          transcriptionActions={transcriptionActions}
          user={user}
          userId={user?.id ?? null}
          onSignOut={onSignOut}
          updateState={updateState}
          updateActions={updateActions}
          notifications={notifications}
          unreadCount={unreadCount}
          onMarkRead={onMarkRead}
          onMarkAllRead={onMarkAllRead}
          onDismissNotification={onDismissNotification}
          onClearAllNotifications={onClearAllNotifications}
        />
        <EngineDownBanner
          engineStatus={engineStatus}
          onRestartEngine={onRestartEngine}
          onOpenMonitor={onOpenMonitor ?? NOOP}
        />
        <AppActionBanner engineStatus={engineStatus} />
        <main className="flex flex-1 flex-col overflow-hidden relative">
          {pages.map(({ path, element }) => (
            <div
              key={path}
              className="flex h-full flex-col overflow-hidden"
              style={{ display: pageIsActive(path, location.pathname) ? "flex" : "none" }}
            >
              {element}
            </div>
          ))}
        </main>
        <StatusBar
          engineStatus={engineStatus}
          engineUrl={engineUrl}
          {...(engineVersion !== undefined ? { engineVersion } : {})}
          onRefresh={onRefresh}
          {...(onOpenMonitor !== undefined ? { onOpenMonitor } : {})}
        />
      </div>
    </div>
  );
}
