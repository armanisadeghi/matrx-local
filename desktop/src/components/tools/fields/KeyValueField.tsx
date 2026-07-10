import { useEffect, useRef, useState } from "react";
import { useFormContext, Controller } from "react-hook-form";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Plus, X } from "lucide-react";
import type { ToolFieldSchema } from "@/types/tool-schema";

interface FieldProps {
  field: ToolFieldSchema;
}

interface Row {
  /** Stable identity for React keys — NEVER the array index or the key
   * string. Renaming a key used to reorder Object.entries and, with
   * index keys, React re-used inputs across rows: the focused input
   * suddenly showed a different entry's data. */
  id: number;
  k: string;
  v: string;
}

/**
 * Inner editor with local row state. The form value is a plain object, but
 * editing happens on an id-keyed row array so rows keep their position and
 * identity while the user types (including mid-rename, duplicate keys, and
 * temporarily-empty keys). Rows are re-derived only when the form value
 * changes externally (e.g. form reset), detected via a last-emitted marker.
 */
function KeyValueRows({
  value,
  onChange,
}: {
  value: Record<string, string> | undefined;
  onChange: (v: Record<string, string>) => void;
}) {
  const idRef = useRef(0);
  const toRows = (obj: Record<string, string> | undefined): Row[] =>
    Object.entries(obj ?? {}).map(([k, v]) => ({
      id: ++idRef.current,
      k,
      v: String(v),
    }));

  const [rows, setRows] = useState<Row[]>(() => toRows(value));
  const lastEmitted = useRef<string>(JSON.stringify(value ?? {}));

  // External reset/patch (not caused by our own commit) → rebuild rows.
  useEffect(() => {
    const incoming = JSON.stringify(value ?? {});
    if (incoming !== lastEmitted.current) {
      lastEmitted.current = incoming;
      setRows(toRows(value));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  const commit = (next: Row[]) => {
    setRows(next);
    const obj: Record<string, string> = {};
    for (const row of next) {
      if (row.k) obj[row.k] = row.v;
    }
    lastEmitted.current = JSON.stringify(obj);
    onChange(obj);
  };

  return (
    <div className="space-y-2">
      {rows.map((row) => (
        <div key={row.id} className="flex gap-2 items-center">
          <Input
            value={row.k}
            onChange={(e) =>
              commit(
                rows.map((r) =>
                  r.id === row.id ? { ...r, k: e.target.value } : r,
                ),
              )
            }
            placeholder="Key"
            className="font-mono text-xs flex-1"
          />
          <Input
            value={row.v}
            onChange={(e) =>
              commit(
                rows.map((r) =>
                  r.id === row.id ? { ...r, v: e.target.value } : r,
                ),
              )
            }
            placeholder="Value"
            className="font-mono text-xs flex-1"
          />
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-8 w-8 shrink-0"
            onClick={() => commit(rows.filter((r) => r.id !== row.id))}
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      ))}
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="w-full text-xs"
        onClick={() => commit([...rows, { id: ++idRef.current, k: "", v: "" }])}
      >
        <Plus className="h-3.5 w-3.5" />
        Add Entry
      </Button>
    </div>
  );
}

export function KeyValueField({ field }: FieldProps) {
  const { control } = useFormContext();

  return (
    <div className="space-y-1.5">
      <Label className="text-sm">
        {field.label}
        {field.required && <span className="text-destructive ml-0.5">*</span>}
      </Label>
      <Controller
        name={field.name}
        control={control}
        render={({ field: rhf }) => (
          <KeyValueRows value={rhf.value} onChange={rhf.onChange} />
        )}
      />
      {field.description && (
        <p className="text-xs text-muted-foreground">{field.description}</p>
      )}
    </div>
  );
}
