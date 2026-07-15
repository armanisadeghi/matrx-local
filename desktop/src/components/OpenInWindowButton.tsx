/**
 * OpenInWindowButton — "pop this page out into its own window".
 *
 * Renders only when (a) running in Tauri and (b) the current route maps to a
 * panel page (panels/manifest.tsx ROUTE_TO_PANEL). Reopening an already-open
 * panel focuses it (Rust enforces one panel per page).
 *
 * Must render inside HashRouter (uses useLocation).
 */

import { useCallback } from "react";
import { useLocation } from "react-router-dom";
import { PictureInPicture2 } from "lucide-react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { isTauri, invokeTauri } from "@/lib/sidecar";
import { ROUTE_TO_PANEL } from "@/panels/manifest";

export function OpenInWindowButton() {
  const location = useLocation();
  const page = ROUTE_TO_PANEL[location.pathname];

  const openPanel = useCallback(async () => {
    if (!page) return;
    try {
      await invokeTauri<string>("open_panel_window", { page });
    } catch (err) {
      console.error("[open-in-window] open_panel_window failed:", err);
    }
  }, [page]);

  if (!isTauri() || !page) return null;

  return (
    <Tooltip delayDuration={150}>
      <TooltipTrigger asChild>
        <button
          onClick={() => void openPanel()}
          className="relative flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
        >
          <PictureInPicture2 className="h-4 w-4" />
        </button>
      </TooltipTrigger>
      <TooltipContent>Open this page in a new window</TooltipContent>
    </Tooltip>
  );
}
