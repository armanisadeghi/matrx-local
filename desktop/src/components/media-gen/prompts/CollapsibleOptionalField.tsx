import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { NamedList } from "@/lib/list-library/types";
import { FieldInfoButton } from "./LabelWithInfo";
import { VariablePromptTextarea } from "./VariablePromptTools";
import {
  NEGATIVE_PROMPT_DEFAULT_ROWS,
  ResizablePromptTextarea,
} from "./ResizablePromptTextarea";

export function CollapsibleOptionalField({
  storageKey,
  label,
  info,
  value,
  onChange,
  placeholder,
  enableVariables = false,
  onVariableInsert,
}: {
  storageKey: string;
  label: string;
  info: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  enableVariables?: boolean;
  onVariableInsert?: (list: NamedList, value: string) => void;
}) {
  const [open, setOpen] = useState(() => readOpen(storageKey));

  useEffect(() => {
    try {
      localStorage.setItem(storageKey, open ? "1" : "0");
    } catch {
      // ignore
    }
  }, [open, storageKey]);

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 gap-1 px-1 text-xs text-muted-foreground hover:text-foreground"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
        >
          {open ? (
            <ChevronDown className="h-3.5 w-3.5" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5" />
          )}
          {label}
        </Button>
        <FieldInfoButton label={label} info={info} />
      </div>
      {open &&
        (enableVariables ? (
          <VariablePromptTextarea
            value={value}
            onChange={onChange}
            {...(onVariableInsert ? { onVariableInsert } : {})}
            resizeStorageKey={`${storageKey}:editor`}
            defaultRows={NEGATIVE_PROMPT_DEFAULT_ROWS}
            className="text-sm"
            placeholder={placeholder}
          />
        ) : (
          <ResizablePromptTextarea
            value={value}
            onChange={(e) => onChange(e.target.value)}
            resizeStorageKey={`${storageKey}:editor`}
            defaultRows={NEGATIVE_PROMPT_DEFAULT_ROWS}
            className="text-sm"
            placeholder={placeholder}
          />
        ))}
    </div>
  );
}

function readOpen(key: string): boolean {
  try {
    return localStorage.getItem(key) === "1";
  } catch {
    return false;
  }
}
