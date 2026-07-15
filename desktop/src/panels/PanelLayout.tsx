/**
 * PanelLayout — slim chrome for lightweight panel windows.
 *
 * No sidebar, no global nav: a single header row with the page title, the
 * engine status dot, and an "Open Main Window" affordance, then the page
 * content. Panels are companions to a full window, not replacements.
 */

import type { ReactNode } from "react";
import { useCallback } from "react";
import { AppWindow } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { invokeTauri } from "@/lib/sidecar";
import type { EngineStatus } from "@/hooks/use-engine";

const STATUS_DOT: Record<EngineStatus, string> = {
  connected: "bg-emerald-500",
  discovering: "bg-amber-500",
  starting: "bg-amber-500",
  disconnected: "bg-red-500",
  error: "bg-red-500",
};

export function PanelLayout({
  title,
  status,
  children,
}: {
  title: string;
  status: EngineStatus;
  children: ReactNode;
}) {
  const openMainWindow = useCallback(async () => {
    try {
      await invokeTauri<void>("focus_app_window", { label: "main" });
    } catch {
      // Main window was closed while peers/panels lived on — open a fresh
      // full window instead of failing silently.
      try {
        await invokeTauri<string>("open_peer_window");
      } catch (err) {
        console.error("[panel] could not open a full window:", err);
      }
    }
  }, []);

  return (
    <div className="flex h-screen flex-col bg-background text-foreground">
      <header className="flex h-11 shrink-0 items-center gap-3 border-b border-border px-4">
        <span
          className={`h-2 w-2 shrink-0 rounded-full ${STATUS_DOT[status]}`}
          aria-label={`Engine ${status}`}
        />
        <h1 className="truncate text-sm font-medium">{title}</h1>
        <div className="ml-auto">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                onClick={() => void openMainWindow()}
              >
                <AppWindow className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Open main window</TooltipContent>
          </Tooltip>
        </div>
      </header>
      <main className="min-h-0 flex-1 overflow-auto">{children}</main>
    </div>
  );
}
