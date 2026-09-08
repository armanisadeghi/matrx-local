import { useFormContext } from "react-hook-form";
import { BasicInput as Input, Label } from "@ai-matrx/design-system";
import type { ToolFieldSchema } from "@/types/tool-schema";

interface FieldProps {
  field: ToolFieldSchema;
}

export function NumberField({ field }: FieldProps) {
  const { register, formState: { errors } } = useFormContext();

  const rangeHint =
    field.min !== undefined && field.max !== undefined
      ? `${field.min} – ${field.max}`
      : field.min !== undefined
        ? `min ${field.min}`
        : field.max !== undefined
          ? `max ${field.max}`
          : null;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <Label htmlFor={field.name} className="text-sm">
          {field.label}
          {field.required && <span className="text-destructive ml-0.5">*</span>}
        </Label>
        {rangeHint && (
          <span className="text-[10px] text-muted-foreground font-mono bg-muted/40 px-1.5 py-0.5 rounded border border-border/40">
            {rangeHint}
          </span>
        )}
        {field.defaultValue !== undefined && (
          <span className="text-[10px] text-muted-foreground">
            default: <span className="font-mono">{String(field.defaultValue)}</span>
          </span>
        )}
      </div>
      <Input
        id={field.name}
        type="number"
        placeholder={
          field.placeholder ??
          (field.defaultValue !== undefined ? String(field.defaultValue) : undefined)
        }
        min={field.min}
        max={field.max}
        {...register(field.name, {
          setValueAs: (v) => {
            const n = Number(v);
            // Return undefined for empty/NaN so the field is omitted rather than sending NaN
            if (v === "" || v === null || v === undefined || isNaN(n)) return undefined;
            return n;
          },
        })}
        className="font-mono text-sm w-36"
      />
      {field.description && (
        <p className="text-xs text-muted-foreground">{field.description}</p>
      )}
      {errors[field.name] && (
        <p className="text-xs text-destructive">
          {errors[field.name]?.message as string}
        </p>
      )}
    </div>
  );
}
