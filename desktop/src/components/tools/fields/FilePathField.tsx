import { useCallback } from "react";
import { useFormContext } from "react-hook-form";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { FolderOpen, X } from "lucide-react";
import type { ToolFieldSchema } from "@/types/tool-schema";

interface FieldProps {
  field: ToolFieldSchema;
}

// Try to use Tauri file dialog; gracefully falls back in browser mode
async function openFilePicker(directory?: boolean): Promise<string | null> {
  try {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const result = await open({ directory: directory ?? false, multiple: false });
    if (typeof result === "string") return result;
    if (Array.isArray(result) && (result as string[]).length > 0) return (result as string[])[0] ?? null;
    return null;
  } catch {
    // Running in browser (not Tauri) — return null so user types manually
    return null;
  }
}

export function FilePathField({ field }: FieldProps) {
  const { register, watch, setValue, formState: { errors } } = useFormContext();
  const current = watch(field.name) as string | undefined;

  const handlePick = useCallback(async () => {
    const picked = await openFilePicker(field.allowDirectory);
    if (picked) setValue(field.name, picked, { shouldValidate: true });
  }, [field.name, field.allowDirectory, setValue]);

  const handleClear = useCallback(() => {
    setValue(field.name, "", { shouldValidate: true });
  }, [field.name, setValue]);

  return (
    <div className="space-y-1.5">
      <Label htmlFor={field.name} className="text-sm font-medium">
        {field.label}
        {field.required && <span className="text-destructive ml-0.5">*</span>}
      </Label>

      {current ? (
        // Show selected path as a chip
        <div className="flex items-center gap-2 rounded-lg border bg-muted/40 px-3 py-2">
          <FolderOpen className="h-4 w-4 text-muted-foreground shrink-0" />
          <span className="flex-1 truncate font-mono text-xs text-foreground">{current}</span>
          <button type="button" onClick={handleClear} className="text-muted-foreground hover:text-destructive transition-colors">
            <X className="h-3.5 w-3.5" />
          </button>
          <input type="hidden" {...register(field.name)} />
        </div>
      ) : (
        <div className="flex gap-2">
          <Input
            id={field.name}
            type="text"
            placeholder={field.placeholder ?? "/path/to/file"}
            {...register(field.name)}
            className="font-mono text-xs flex-1"
          />
          <Button
            type="button"
            variant="outline"
            size="icon"
            onClick={handlePick}
            title="Browse…"
            className="shrink-0"
          >
            <FolderOpen className="h-4 w-4" />
          </Button>
        </div>
      )}

      {field.description && (
        <p className="text-xs text-muted-foreground">{field.description}</p>
      )}
      {errors[field.name] && (
        <p className="text-xs text-destructive">{errors[field.name]?.message as string}</p>
      )}
    </div>
  );
}
