import { useState, useRef, useCallback, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Play, RotateCcw, Code, FormInput, Loader2 } from "lucide-react";
import { ToolForm } from "./ToolForm";
import { ToolOutput } from "./ToolOutput";
import { ToolInfoPanel } from "./ToolInfoPanel";
import { JsonFallbackEditor } from "./JsonFallbackEditor";
import type { ToolUISchema } from "@/types/tool-schema";

interface ToolDetailPanelProps {
  schema: ToolUISchema;
  loading: boolean;
  result: unknown | null;
  error: string | null;
  elapsedMs: number;
  onInvoke: (toolName: string, params: Record<string, unknown>) => void;
  onReset: () => void;
}

export function ToolDetailPanel({
  schema,
  loading,
  result,
  error,
  elapsedMs,
  onInvoke,
  onReset,
}: ToolDetailPanelProps) {
  const [mode, setMode] = useState<"form" | "json">("form");
  const [jsonInput, setJsonInput] = useState("{}");
  const formRef = useRef<HTMLFormElement | null>(null);

  // Reset JSON input when switching to a different tool
  useEffect(() => {
    setJsonInput("{}");
  }, [schema.toolName]);

  const handleFormSubmit = useCallback(
    (values: Record<string, unknown>) => {
      onInvoke(schema.toolName, values);
    },
    [schema.toolName, onInvoke],
  );

  const handleJsonSubmit = useCallback(() => {
    try {
      const parsed = JSON.parse(jsonInput);
      onInvoke(schema.toolName, parsed);
    } catch {
      // Invalid JSON — silently ignore
    }
  }, [schema.toolName, jsonInput, onInvoke]);

  const handleRun = useCallback(() => {
    if (mode === "json") {
      handleJsonSubmit();
    } else {
      // Trigger the form's submit
      formRef.current?.requestSubmit();
    }
  }, [mode, handleJsonSubmit]);

  return (
    <div className="flex h-full flex-col">
      {/* Mode toggle */}
      <div className="flex items-center justify-end px-4 py-2 border-b">
        <Button
          variant="ghost"
          size="sm"
          className="h-7 text-xs gap-1"
          onClick={() => setMode(mode === "form" ? "json" : "form")}
        >
          {mode === "form" ? (
            <>
              <Code className="h-3 w-3" /> Raw JSON
            </>
          ) : (
            <>
              <FormInput className="h-3 w-3" /> Form
            </>
          )}
        </Button>
      </div>

      {/* Form / JSON Area */}
      <ScrollArea className="flex-1">
        <div className="p-4 space-y-4">
          {mode === "form" ? (
            <div
              ref={(el) => {
                formRef.current = el?.querySelector("form") ?? null;
              }}
            >
              <ToolForm
                key={schema.toolName}
                schema={schema}
                onSubmit={handleFormSubmit}
                loading={loading}
              />
            </div>
          ) : (
            <JsonFallbackEditor value={jsonInput} onChange={setJsonInput} />
          )}

          <Separator />

          {/* Action Buttons */}
          <div className="flex gap-2">
            <Button onClick={handleRun} disabled={loading} className="flex-1">
              {loading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Play className="h-4 w-4" />
              )}
              {loading ? "Running..." : "Run"}
            </Button>
            <Button
              variant="outline"
              size="icon"
              onClick={onReset}
              disabled={loading}
            >
              <RotateCcw className="h-4 w-4" />
            </Button>
          </div>

          {/* Output */}
          {(result || error) && (
            <ToolOutput
              result={error ? { type: "error", output: error } : result}
              outputType={schema.outputType}
              elapsedMs={elapsedMs}
              toolName={schema.toolName}
            />
          )}
        </div>
      </ScrollArea>

      {/* Tool reference info */}
      <ToolInfoPanel schema={schema} />
    </div>
  );
}
