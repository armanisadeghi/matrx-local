/**
 * NamedListPicker — canonical list selection + add-new popover (ListLibraryCore).
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
import { useListLibraryApp } from "@/contexts/ListLibraryContext";
import type { NamedList } from "@/lib/list-library/types";
import {
  enabledOptionCountForList,
  formatListSelectLabel,
} from "@/lib/list-library/display";
import { ListLibrarySurface } from "../ListLibrarySurface";
import { NO_LIST_ID, PICKER_ADD_NEW } from "./constants";

export interface NamedListPickerProps {
  value: string;
  onChange: (listId: string) => void;
  placeholder?: string;
  className?: string;
  /** Hide the adjacent add-new button (e.g. when space is tight). */
  hideAddNew?: boolean;
}

export function NamedListPicker({
  value,
  onChange,
  placeholder = "Pick list…",
  className,
  hideAddNew = false,
}: NamedListPickerProps) {
  const [state] = useListLibraryApp();
  const [libraryOpen, setLibraryOpen] = useState(false);

  const selected =
    value !== NO_LIST_ID
      ? state.lists.find((row) => row.id === value)
      : undefined;

  const selectValue = value !== NO_LIST_ID && !selected ? NO_LIST_ID : value;

  const handleSelect = (next: string) => {
    if (next === PICKER_ADD_NEW) {
      setLibraryOpen(true);
      return;
    }
    onChange(next);
  };

  return (
    <div className={`flex min-w-0 items-center gap-1.5 ${className ?? ""}`}>
      <Select value={selectValue} onValueChange={handleSelect}>
        <SelectTrigger className="h-8 min-w-0 flex-1 text-xs">
          <SelectValue placeholder={placeholder}>
            {selected ? formatListSelectLabel(selected) : placeholder}
          </SelectValue>
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={NO_LIST_ID}>{placeholder}</SelectItem>
          {state.lists.map((list) => (
            <SelectItem key={list.id} value={list.id}>
              {formatListSelectLabel(list)}
            </SelectItem>
          ))}
          <SelectItem value={PICKER_ADD_NEW} className="text-primary">
            + Add new list…
          </SelectItem>
        </SelectContent>
      </Select>

      {!hideAddNew && (
        <ListLibrarySurface
          surface="popover"
          open={libraryOpen}
          onOpenChange={setLibraryOpen}
          title="Lists"
          description="Create or edit option lists — same UI as the Lists tab."
          trigger={
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  className="h-8 w-8 shrink-0"
                  aria-label="Add new list"
                >
                  <Plus className="h-3.5 w-3.5" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Add new list</TooltipContent>
            </Tooltip>
          }
        />
      )}
    </div>
  );
}

export function resolveListOptionCount(
  lists: readonly NamedList[],
  listId: string,
): number | null {
  if (listId === NO_LIST_ID) return null;
  const row = lists.find((item) => item.id === listId);
  return row ? enabledOptionCountForList(row) : null;
}
