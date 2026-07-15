/**
 * MenuEventBridge — routes native-menu events into the SPA.
 *
 * The Rust menu (src-tauri/src/menu.rs) emits `menu://…` events to the
 * FOCUSED window only, so with multiple windows exactly one bridge reacts.
 * Must render INSIDE HashRouter (uses useNavigate).
 *
 * Events handled here:
 *   • menu://open-settings   → navigate to /settings
 *   • menu://move-to-window  → reopen the current page as a panel window
 *     (wired in the panels phase; listener registered by PanelBridge there)
 */

import { useEffect, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { isTauri, invokeTauri } from "@/lib/sidecar";
import { ROUTE_TO_PANEL } from "@/panels/manifest";

export function MenuEventBridge() {
  const navigate = useNavigate();
  const location = useLocation();
  // Route read via ref so the listeners register once and never re-subscribe.
  const pathRef = useRef(location.pathname);
  pathRef.current = location.pathname;

  useEffect(() => {
    if (!isTauri()) return;
    let cancelled = false;
    const unlistens: Array<() => void> = [];

    void (async () => {
      const { listen } = await import("@tauri-apps/api/event");

      const register = async (event: string, handler: () => void) => {
        const un = await listen(event, handler);
        if (cancelled) {
          un();
        } else {
          unlistens.push(un);
        }
      };

      await register("menu://open-settings", () => {
        navigate("/settings");
      });

      await register("menu://move-to-window", () => {
        const page = ROUTE_TO_PANEL[pathRef.current];
        if (!page) return; // page has no panel form — menu pick is a no-op
        void invokeTauri<string>("open_panel_window", { page })
          .then(() => {
            // "Move" semantics: the page now lives in its own window;
            // step the source window back to the dashboard.
            navigate("/");
          })
          .catch((err) =>
            console.error("[menu] open_panel_window failed:", err),
          );
      });
    })();

    return () => {
      cancelled = true;
      unlistens.forEach((u) => u());
    };
  }, [navigate]);

  return null;
}
