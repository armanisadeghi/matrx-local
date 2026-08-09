/**
 * Open a URL in the user's real browser — the ONE implementation.
 *
 * Inside Tauri, `window.open` on an external URL either does nothing or
 * navigates the app's own webview away from the app. The shell plugin hands it
 * to the OS instead. Outside Tauri (`pnpm dev` in a browser tab) there is no
 * plugin, so a plain new tab is the honest fallback.
 *
 * Every surface that shows a link the user can follow uses this: nothing in
 * this app names a reachable URL and then refuses to reach it.
 */
export async function openExternal(url: string): Promise<void> {
  if (!url) return;
  if (
    typeof window !== "undefined" &&
    (window as unknown as Record<string, unknown>).__TAURI__
  ) {
    const { open } = await import("@tauri-apps/plugin-shell");
    await open(url);
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}
