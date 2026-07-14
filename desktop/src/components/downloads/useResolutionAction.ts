/**
 * The single dispatcher for a DownloadResolution's action.
 *
 * A download can fail because a key isn't set, a model license hasn't been
 * accepted, or the AI packages aren't installed. None of those are errors to
 * report at the user — they're questions to ask them. The engine sends a
 * `resolution` (app/services/downloads/failures.py) describing what happened
 * in plain language plus the single action that fixes it; the UI renders it
 * (ActionNeededCard in DownloadManagerModal) and dispatches the action here.
 *
 * Adding a new self-fixable failure means adding a constructor in failures.py
 * and (only if it needs a new KIND of action) a case in the switch below.
 */

import { useCallback } from "react";
import { useNavigate } from "react-router-dom";

import type { DownloadResolution } from "@/lib/downloads/types";
import { emitClientLog } from "@/hooks/use-unified-log";

export function useResolutionAction(): (
  resolution: DownloadResolution,
) => Promise<void> {
  const navigate = useNavigate();
  return useCallback(
    async (resolution: DownloadResolution) => {
      switch (resolution.action_kind) {
        case "settings_api_keys":
          navigate(
            `/settings?tab=api-keys${
              resolution.provider ? `&provider=${resolution.provider}` : ""
            }`,
          );
          break;
        case "open_url":
          if (resolution.action_url) {
            // The system browser — a model license can only be accepted while
            // signed in to Hugging Face, which only their site can do.
            const { open } = await import("@tauri-apps/plugin-shell");
            await open(resolution.action_url);
          }
          break;
        case "install_ai_packages":
          navigate("/media-generation");
          break;
        default: {
          // A resolution kind this build doesn't know — surface it rather than
          // silently doing nothing when the user clicks the button.
          const unknown: never = resolution.action_kind;
          emitClientLog(
            "error",
            `[downloads] unknown resolution action_kind: ${String(unknown)}`,
            "downloads",
          );
        }
      }
    },
    [navigate],
  );
}
