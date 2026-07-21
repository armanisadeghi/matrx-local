import type { ReactNode } from "react";
import { SavedPromptsCore } from "./core/saved-prompts/SavedPromptsCore";
import { MediaGenSurface } from "./surfaces/MediaGenSurface";
import type { SavedPrompt } from "@/lib/saved-prompts/types";
import type { SavedPromptsCoreProps } from "./core/saved-prompts/SavedPromptsCore";

/** Full-page tab host for saved prompts. */
export function SavedPromptsSection(
  props?: Omit<SavedPromptsCoreProps, "intent">,
) {
  return <SavedPromptsCore intent="manage" showStoragePath {...props} />;
}

export interface SavedPromptsSurfaceProps extends SavedPromptsCoreProps {
  surface: "dialog" | "popover";
  open: boolean;
  onOpenChange: (open: boolean) => void;
  trigger?: ReactNode;
  title?: string;
  description?: string;
  onPick?: (prompt: SavedPrompt) => void;
}

/** Dialog or popover host — same core as the tab. */
export function SavedPromptsSurface({
  surface,
  open,
  onOpenChange,
  trigger,
  title = "Saved prompts",
  description,
  intent = "pick",
  onPick,
  ...coreProps
}: SavedPromptsSurfaceProps) {
  const handlePick = (prompt: SavedPrompt) => {
    onPick?.(prompt);
    onOpenChange(false);
  };

  return (
    <MediaGenSurface
      kind={surface}
      open={open}
      onOpenChange={onOpenChange}
      trigger={trigger}
      title={title}
      description={
        description ??
        (intent === "pick"
          ? "Choose a saved prompt to fill the form."
          : "Name and store prompt text for reuse.")
      }
      contentClassName="p-3"
    >
      <SavedPromptsCore
        intent={intent}
        onPick={handlePick}
        showStoragePath={false}
        {...coreProps}
      />
    </MediaGenSurface>
  );
}
