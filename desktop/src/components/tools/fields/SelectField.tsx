import { useFormContext, Controller } from "react-hook-form";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Label } from "@ai-matrx/design-system";
import type { ToolFieldSchema } from "@/types/tool-schema";

interface FieldProps {
  field: ToolFieldSchema;
}

export function SelectField({ field }: FieldProps) {
  const { control, formState: { errors } } = useFormContext();

  return (
    <div className="space-y-1.5">
      <Label htmlFor={field.name} className="text-sm">
        {field.label}
        {field.required && <span className="text-destructive ml-0.5">*</span>}
      </Label>
      <Controller
        name={field.name}
        control={control}
        render={({ field: rhf }) => (
          <Select value={rhf.value ?? ""} onValueChange={rhf.onChange}>
            <SelectTrigger className="w-full">
              <SelectValue placeholder={field.placeholder ?? "Select..."} />
            </SelectTrigger>
            <SelectContent>
              {(field.options ?? []).map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
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
