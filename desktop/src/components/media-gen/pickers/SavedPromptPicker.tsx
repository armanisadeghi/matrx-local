/**
 * SavedPromptPicker — canonical saved-prompt selection + add-new popover.
 */

import { useState } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useSavedPromptsApp } from "@/contexts/SavedPromptsContext";
import type { SavedPrompt } from "@/lib/saved-prompts/types";
import { SavedPromptsSurface } from "../SavedPromptsSection";
import { NO_SAVED_PROMPT_ID, PICKER_ADD_NEW } from "./constants";

export interface SavedPromptPickerProps {
  value: string;
  onChange: (prompt: SavedPrompt | null) => void;
  className?: string;
}

export function SavedPromptPicker({
  value,
  onChange,
  className,
}: SavedPromptPickerProps) {
  const [state] = useSavedPromptsApp();
  const [promptsOpen, setPromptsOpen] = useState(false);

  const selected =
    value !== NO_SAVED_PROMPT_ID
      ? state.prompts.find((row) => row.id === value)
      : undefined;

  const selectValue =
    value !== NO_SAVED_PROMPT_ID && !selected ? NO_SAVED_PROMPT_ID : value;

  const handleSelect = (next: string) => {
    if (next === PICKER_ADD_NEW) {
      setPromptsOpen(true);
      return;
    }
    if (next === NO_SAVED_PROMPT_ID) {
      onChange(null);
      return;
    }
    const row = state.prompts.find((item) => item.id === next);
    if (row) onChange(row);
  };

  return (
    <div className={`flex min-w-0 items-center gap-1.5 ${className ?? ""}`}>
      <Select value={selectValue} onValueChange={handleSelect}>
        <SelectTrigger className="h-9 min-w-0 flex-1">
          <SelectValue placeholder="Type manually">
            {selected?.name ?? "Type manually"}
          </SelectValue>
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={NO_SAVED_PROMPT_ID}>Type manually</SelectItem>
          {state.prompts.map((row) => (
            <SelectItem key={row.id} value={row.id}>
              {row.name}
            </SelectItem>
          ))}
          <SelectItem value={PICKER_ADD_NEW} className="text-primary">
            + Add new prompt…
          </SelectItem>
        </SelectContent>
      </Select>

      <SavedPromptsSurface
        surface="popover"
        open={promptsOpen}
        onOpenChange={setPromptsOpen}
        intent="manage"
        title="Saved prompts"
        description="Create or edit prompts — same UI as the Prompts tab."
        trigger={
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="outline"
                size="icon"
                className="h-9 w-9 shrink-0"
                aria-label="Add new prompt"
              >
                <Plus className="h-3.5 w-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Add new prompt</TooltipContent>
          </Tooltip>
        }
      />
    </div>
  );
}
