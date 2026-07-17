import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { windowRole } from "./lib/window-role";
import { TranscriptOverlay } from "./components/TranscriptOverlay";
import { PanelApp } from "./panels/PanelApp";
import { startAppRuntimeConfig } from "./lib/app-config";
import "./index.css";

/**
 * Window-role entry branch (see lib/window-role.ts):
 *   • overlay → ONLY the TranscriptOverlay — no providers, no engine socket,
 *               no background tasks. The overlay used to boot the entire app
 *               (14 providers + its own WebSocket); never regress that.
 *   • panel   → PanelApp: slim chrome + only the providers the page needs
 *               (panels/manifest.tsx).
 *   • main / peers / browser dev → full App.
 */
const root = ReactDOM.createRoot(document.getElementById("root")!);

// Start the public admin-config refresh without delaying application windows.
// The transcript overlay intentionally owns no background work.
if (windowRole.kind !== "overlay") startAppRuntimeConfig();

const tree =
  windowRole.kind === "overlay" ? (
    <TranscriptOverlay />
  ) : windowRole.kind === "panel" ? (
    <PanelApp page={windowRole.page} />
  ) : (
    <App />
  );

root.render(<React.StrictMode>{tree}</React.StrictMode>);
