import { useMemo, useState } from "react";
import {
  useForm,
  FormProvider,
  useFormContext,
  Controller,
} from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { schemaToZod } from "@/lib/schema-to-zod";
import { TextField } from "./fields/TextField";
import { TextareaField } from "./fields/TextareaField";
import { NumberField } from "./fields/NumberField";
import { BooleanField } from "./fields/BooleanField";
import { SelectField } from "./fields/SelectField";
import { FilePathField } from "./fields/FilePathField";
import { TagsField } from "./fields/TagsField";
import { KeyValueField } from "./fields/KeyValueField";
import { Label, BasicTextarea as Textarea } from "@ai-matrx/design-system";
import type { ToolFieldSchema, ToolUISchema } from "@/types/tool-schema";

/** JSON textarea field with live syntax validation feedback */
function JsonField({ field }: { field: ToolFieldSchema }) {
  const { control } = useFormContext();
  const [jsonError, setJsonError] = useState<string | null>(null);

  return (
    <div className="space-y-1.5">
      <Label htmlFor={field.name} className="text-sm">
        {field.label}
        {field.required && <span className="text-destructive ml-0.5">*</span>}
        <span className="ml-2 text-[10px] font-mono text-muted-foreground bg-muted/40 px-1.5 py-0.5 rounded border border-border/40">
          JSON object
        </span>
      </Label>
      <Controller
        name={field.name}
        control={control}
        render={({ field: rhf }) => (
          <Textarea
            id={field.name}
            value={
              typeof rhf.value === "string"
                ? rhf.value
                : JSON.stringify(rhf.value ?? {}, null, 2)
            }
            onChange={(e) => {
              rhf.onChange(e.target.value);
              // Validate on change so user gets instant feedback
              const trimmed = e.target.value.trim();
              if (!trimmed) {
                setJsonError(null);
                return;
              }
              try {
                JSON.parse(trimmed);
                setJsonError(null);
              } catch (err) {
                setJsonError(
                  err instanceof SyntaxError ? err.message : "Invalid JSON",
                );
              }
            }}
            placeholder={field.placeholder ?? '{\n  "key": "value"\n}'}
            className="font-mono text-xs min-h-[100px] resize-y"
          />
        )}
      />
      {jsonError && (
        <p className="text-xs text-destructive">JSON error: {jsonError}</p>
      )}
      {field.description && !jsonError && (
        <p className="text-xs text-muted-foreground">{field.description}</p>
      )}
    </div>
  );
}

/**
 * Coerce a raw form value to the correct JS type based on the field schema.
 * React-hook-form captures all <input> values as strings unless explicitly
 * configured otherwise (e.g. valueAsNumber). This pass ensures booleans,
 * numbers, arrays, and objects are sent with the right types.
 */
function coerceValue(value: unknown, field: ToolFieldSchema): unknown {
  if (value === undefined || value === null) return value;

  switch (field.type) {
    case "number": {
      if (typeof value === "number") return value;
      const n = Number(value);
      return isNaN(n) ? undefined : n;
    }

    case "boolean": {
      if (typeof value === "boolean") return value;
      if (value === "true") return true;
      if (value === "false") return false;
      return Boolean(value);
    }

    case "json": {
      // Textarea fields for json type submit a string — parse it
      if (typeof value === "string") {
        const trimmed = value.trim();
        if (!trimmed) return undefined;
        try {
          return JSON.parse(trimmed);
        } catch {
          // Invalid JSON — return as-is and let server surface the error
          return trimmed;
        }
      }
      return value;
    }

    case "tags": {
      // Tags are already string[] — but if items need to be numbers, handle that
      if (Array.isArray(value)) return value;
      if (typeof value === "string" && value)
        return value.split(",").map((s) => s.trim());
      return [];
    }

    default:
      // text, textarea, code, file-path, select, key-value — keep as-is
      return value;
  }
}

interface ToolFormProps {
  schema: ToolUISchema;
  onSubmit: (values: Record<string, unknown>) => void;
  loading?: boolean;
}

function renderField(field: ToolFieldSchema) {
  switch (field.type) {
    case "text":
      return <TextField key={field.name} field={field} />;
    case "textarea":
    case "code":
      return <TextareaField key={field.name} field={field} />;
    case "number":
      return <NumberField key={field.name} field={field} />;
    case "boolean":
      return <BooleanField key={field.name} field={field} />;
    case "select":
      return <SelectField key={field.name} field={field} />;
    case "file-path":
      return <FilePathField key={field.name} field={field} />;
    case "tags":
      return <TagsField key={field.name} field={field} />;
    case "key-value":
      return <KeyValueField key={field.name} field={field} />;
    case "json":
      return <JsonField key={field.name} field={field} />;
    default:
      return <TextField key={field.name} field={field} />;
  }
}

export function ToolForm({ schema, onSubmit, loading }: ToolFormProps) {
  const zodSchema = useMemo(() => schemaToZod(schema.fields), [schema.fields]);

  const defaultValues = useMemo(() => {
    const values: Record<string, unknown> = {};
    for (const field of schema.fields) {
      if (field.defaultValue !== undefined) {
        values[field.name] = field.defaultValue;
      } else if (field.type === "boolean") {
        values[field.name] = false;
      } else if (field.type === "tags") {
        values[field.name] = [];
      } else if (field.type === "key-value") {
        values[field.name] = {};
      }
    }
    return values;
  }, [schema.fields]);

  const methods = useForm({
    resolver: zodResolver(zodSchema),
    defaultValues,
  });

  const handleSubmit = methods.handleSubmit((data) => {
    // Build a name→field map for O(1) lookup
    const fieldByName = new Map(schema.fields.map((f) => [f.name, f]));

    // Coerce each value to its correct type, then strip empties
    const cleaned: Record<string, unknown> = {};
    for (const [key, raw] of Object.entries(data)) {
      const field = fieldByName.get(key);
      const value = field ? coerceValue(raw, field) : raw;

      if (value === undefined || value === null) continue;
      if (typeof value === "string" && value === "") continue;
      if (typeof value === "number" && isNaN(value)) continue;
      cleaned[key] = value;
    }
    onSubmit(cleaned);
  });

  return (
    <FormProvider {...methods}>
      <form onSubmit={handleSubmit} className="space-y-4">
        {schema.fields.map(renderField)}
        <input type="submit" hidden disabled={loading} />
      </form>
    </FormProvider>
  );
}

// Export the form ref handle for external submit triggering
export { type ToolFormProps };
