/**
 * BrowserRuntimeContext — one owner of the "is the built-in browser installed"
 * state and of the install that fixes it.
 *
 * Singleton because more than one surface asks the same question (the Scraping
 * page's banner and method selector today; any JS-rendering feature tomorrow),
 * and because an install started from one of them must show its progress in all
 * of them. It also registers the `install_browser_engine` handler so an
 * ActionNeeded card raised anywhere — chat, a tool result, the recovery
 * surface — installs with one click instead of only navigating.
 */

import { createContext, useContext, useEffect, type ReactNode } from "react";

import {
  useBrowserRuntime,
  type UseBrowserRuntimeReturn,
} from "@/hooks/use-browser-runtime";
import {
  navigateForActionNeeded,
  registerActionNeededHandler,
} from "@/features/action-needed/actions";

const BrowserRuntimeContext = createContext<UseBrowserRuntimeReturn | null>(
  null,
);

export function BrowserRuntimeProvider({ children }: { children: ReactNode }) {
  const value = useBrowserRuntime();
  const { install } = value.actions;

  useEffect(() => {
    return registerActionNeededHandler("install_browser_engine", async () => {
      // Show the user where the work is happening, then do it.
      await navigateForActionNeeded("/scraping");
      await install();
    });
  }, [install]);

  return (
    <BrowserRuntimeContext.Provider value={value}>
      {children}
    </BrowserRuntimeContext.Provider>
  );
}

export function useBrowserRuntimeContext(): UseBrowserRuntimeReturn {
  const ctx = useContext(BrowserRuntimeContext);
  if (!ctx) {
    throw new Error(
      "useBrowserRuntimeContext must be used inside <BrowserRuntimeProvider>",
    );
  }
  return ctx;
}

/**
 * Retry the mount-time probe after engine discovery succeeds.
 *
 * The provider stays mounted across engine reconnects, so its first probe can
 * run before an engine base URL exists. This keeps that one shared store and
 * refreshes it on the connection transition without adding a polling loop.
 */
export function useBrowserRuntimeConnectionRefresh(connected: boolean): void {
  const {
    actions: { refresh },
  } = useBrowserRuntimeContext();

  useEffect(() => {
    if (connected) void refresh();
  }, [connected, refresh]);
}

export function useOptionalBrowserRuntimeContext(): UseBrowserRuntimeReturn | null {
  return useContext(BrowserRuntimeContext);
}
