import { useState, useCallback } from "react";
import { useFormContext, Controller } from "react-hook-form";
import { Badge, BasicInput as Input, Label } from "@ai-matrx/design-system";
import { X } from "lucide-react";
import type { ToolFieldSchema } from "@/types/tool-schema";

interface FieldProps {
  field: ToolFieldSchema;
}

export function TagsField({ field }: FieldProps) {
  const { control, formState: { errors } } = useFormContext();
  const fieldError = errors[field.name] as
    | { message?: string; [i: number]: { message?: string } | undefined }
    | undefined;
  const errorMessage =
    fieldError?.message ??
    (Array.isArray(fieldError)
      ? fieldError.find((e) => e?.message)?.message
      : undefined);
  const [inputValue, setInputValue] = useState("");

  const addTag = useCallback(
    (tags: string[], onChange: (v: string[]) => void) => {
      const value = inputValue.trim();
      // Repeats are real values (a region of 0, 0, 800, 600).
      if (value) {
        onChange([...tags, value]);
        setInputValue("");
      }
    },
    [inputValue]
  );

  return (
    <div className="space-y-1.5">
      <Label htmlFor={field.name} className="text-sm">
        {field.label}
        {field.required && <span className="text-destructive ml-0.5">*</span>}
      </Label>
      <Controller
        name={field.name}
        control={control}
        render={({ field: rhf }) => {
          const tags: string[] = rhf.value ?? [];
          return (
            <div className="space-y-2">
              <Input
                id={field.name}
                type="text"
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addTag(tags, rhf.onChange);
                  }
                }}
                placeholder={field.placeholder ?? "Type and press Enter"}
                className="text-sm"
              />
              {tags.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {tags.map((tag, index) => (
                    <Badge
                      key={`${index}-${tag}`}
                      variant="secondary"
                      className="text-xs gap-1 pr-1"
                    >
                      {tag}
                      <button
                        type="button"
                        className="rounded-full hover:bg-foreground/10 p-0.5"
                        onClick={() =>
                          rhf.onChange(tags.filter((_, i) => i !== index))
                        }
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </Badge>
                  ))}
                </div>
              )}
            </div>
          );
        }}
      />
      {field.description && (
        <p className="text-xs text-muted-foreground">{field.description}</p>
      )}
      {errorMessage && (
        <p className="text-xs text-destructive">{errorMessage}</p>
      )}
    </div>
  );
}
