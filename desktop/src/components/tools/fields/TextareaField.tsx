import { useFormContext } from "react-hook-form";
import { Label, BasicTextarea as Textarea } from "@ai-matrx/design-system";
import type { ToolFieldSchema } from "@/types/tool-schema";

interface FieldProps {
  field: ToolFieldSchema;
}

export function TextareaField({ field }: FieldProps) {
  const { register, formState: { errors } } = useFormContext();

  return (
    <div className="space-y-1.5">
      <Label htmlFor={field.name} className="text-sm">
        {field.label}
        {field.required && <span className="text-destructive ml-0.5">*</span>}
      </Label>
      <Textarea
        id={field.name}
        placeholder={field.placeholder}
        {...register(field.name)}
        className="font-mono text-xs min-h-[80px] resize-y"
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
