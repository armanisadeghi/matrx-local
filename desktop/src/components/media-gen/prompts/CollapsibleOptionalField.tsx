import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { LabelWithInfo } from "./LabelWithInfo";

export function CollapsibleOptionalField({
  storageKey,
  label,
  info,
  value,
  onChange,
  placeholder,
  rows = 3,
}: {
  storageKey: string;
  label: string;
  info: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  rows?: number;
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
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="h-7 gap-1 px-1 text-xs text-muted-foreground hover:text-foreground"
        onClick={() => setOpen((v) => !v)}
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5" />
        )}
        <LabelWithInfo label={label} info={info} />
      </Button>
      {open && (
        <Textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          rows={rows}
          className="text-sm"
          placeholder={placeholder}
        />
      )}
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
