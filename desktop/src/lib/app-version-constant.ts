declare const __APP_VERSION__: string;

/**
 * The version of the renderer bundle **now executing** — baked in at build
 * time by Vite from the release authority in the repository root
 * (`pyproject.toml`). It cannot change while the app is up, which is exactly
 * why it is the right answer to "what am I running?" and the wrong answer to
 * "is there a newer build?" (see `lib/version-facts.ts`).
 *
 * This module holds the constant alone, with no React and no context import,
 * so `VersionStateContext` can consume it without an import cycle. UI code
 * imports `APP_VERSION` / `AppVersion` from `lib/app-version` as before.
 */
export const APP_VERSION = __APP_VERSION__;
