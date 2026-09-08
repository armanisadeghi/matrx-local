import { Label, BasicTextarea as Textarea } from "@ai-matrx/design-system";

interface JsonFallbackEditorProps {
  value: string;
  onChange: (value: string) => void;
}

export function JsonFallbackEditor({ value, onChange }: JsonFallbackEditorProps) {
  return (
    <div className="space-y-1.5">
      <Label className="text-sm">JSON Input</Label>
      <Textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="{}"
        className="font-mono text-xs min-h-[200px] resize-y"
      />
      <p className="text-xs text-muted-foreground">
        Enter raw JSON parameters for this tool
      </p>
    </div>
  );
}
