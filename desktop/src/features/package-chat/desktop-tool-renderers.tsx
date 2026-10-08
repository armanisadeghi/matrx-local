/**
 * Matrx Local's own tool rows in the package chat, registered into `@ai-matrx/chat`'s renderer
 * registry (the extension does the same for `sleep` / `chrome_batch`):
 *
 * - filesystem results from this computer's engine render as the desktop's file list, and its
 *   "reference" action appends the paths to the conversation's draft (`appendToComposerDraft`);
 * - `google_email_send` — parked by THIS engine for the person's review (`engineHeldTools` in
 *   `./host`) — renders the desktop's review card while the engine holds the call. The card is the
 *   authorization; nothing is sent without its Send.
 *
 * Anything else falls back to the package's generic row.
 */

import { appendToComposerDraft } from "@ai-matrx/chat/agents/redux/execution-system/instance-user-input/append-to-composer-draft.thunk";
import { useAppDispatch } from "@ai-matrx/chat/store/hooks";
import { GenericRenderer } from "@ai-matrx/chat/tool-call-visualization/registry/GenericRenderer";
import { registerToolRenderer } from "@ai-matrx/chat/tool-call-visualization/registry/registry";
import type { ToolRendererProps } from "@ai-matrx/chat/tool-call-visualization/types";
import { FolderOpen, Mail } from "lucide-react";
import { GmailReviewCard } from "@/components/chat/GmailReviewCard";
import { FilesystemResultController } from "@/features/filesystem/FilesystemResultController";
import { referencePathsText } from "@/features/filesystem/reference-paths";
import { normalizeFilesystemPayload } from "@/features/filesystem/tool-results";
import { useEmailReviews } from "@/hooks/use-email-reviews";
import { engine } from "@/lib/api";

/** This engine's filesystem tools whose results are file lists (`app/tools`, `local_*`). */
export const FILESYSTEM_TOOL_NAMES = [
  "local_list_directory",
  "local_find_paths",
  "local_semantic_find_paths",
  "local_filesystem_places",
  "local_glob",
  "local_file",
] as const;

function FilesystemRow(props: ToolRendererProps) {
  const dispatch = useAppDispatch();
  const result = props.entry.status === "completed" ? normalizeFilesystemPayload(props.entry.result) : null;
  const conversationId = props.conversationId;
  if (!result) return <GenericRenderer {...props} />;
  return (
    <FilesystemResultController
      result={result}
      {...(conversationId
        ? { onReference: (paths: string[]) => void dispatch(appendToComposerDraft(conversationId, referencePathsText(paths))) }
        : {})}
    />
  );
}

function GmailReviewRow(props: ToolRendererProps) {
  const [reviews, actions] = useEmailReviews(engine.engineUrl);
  const review = reviews.find((r) => r.callId === props.entry.callId);
  if (!review) return <GenericRenderer {...props} />;
  return <GmailReviewCard review={review} onResolve={actions.resolve} />;
}

let registered = false;
/** Register once per window. */
export function registerDesktopToolRenderers(): void {
  if (registered) return;
  registered = true;
  for (const toolName of FILESYSTEM_TOOL_NAMES) {
    registerToolRenderer(toolName, {
      toolName,
      displayName: "Files",
      icon: FolderOpen,
      chrome: "card",
      InlineComponent: FilesystemRow,
    });
  }
  registerToolRenderer("google_email_send", {
    toolName: "google_email_send",
    displayName: "Email review",
    icon: Mail,
    chrome: "card",
    InlineComponent: GmailReviewRow,
  });
}
