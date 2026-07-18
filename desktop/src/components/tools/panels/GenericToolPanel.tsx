import { Wrench } from "lucide-react";
import { ToolForm } from "@/components/tools/ToolForm";
import { ToolOutput } from "@/components/tools/ToolOutput";
import { ToolInfoPanel } from "@/components/tools/ToolInfoPanel";
import { AiBadge } from "@/components/tools/panels/AiBadge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Loader2, Play, RotateCcw } from "lucide-react";
import { useRef, useCallback } from "react";
import type { ToolUISchema } from "@/types/tool-schema";

interface GenericToolPanelProps {
  schema: ToolUISchema;
  onInvoke: (
    toolName: string,
    params: Record<string, unknown>,
  ) => Promise<void>;
  loading: boolean;
  result: unknown;
  error: string | null;
  elapsedMs: number;
  onReset: () => void;
}

export function GenericToolPanel({
  schema,
  onInvoke,
  loading,
  result,
  error,
  elapsedMs,
  onReset,
}: GenericToolPanelProps) {
  const formRef = useRef<HTMLFormElement | null>(null);

  const handleFormSubmit = useCallback(
    (values: Record<string, unknown>) => {
      onInvoke(schema.toolName, values);
    },
    [onInvoke, schema.toolName],
  );

  const handleRun = useCallback(() => {
    if (schema.fields.length === 0) {
      onInvoke(schema.toolName, {});
    } else {
      formRef.current?.requestSubmit();
    }
  }, [schema, onInvoke]);

  return (
    <div className="flex h-full flex-col">
      <ScrollArea className="flex-1">
        <div className="px-5 pt-4 pb-5 space-y-4">
          <AiBadge />

          {schema.fields.length === 0 && (
            <p className="text-xs text-muted-foreground">
              This tool requires no inputs. Click Run to execute it.
            </p>
          )}

          {schema.fields.length > 0 && (
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
          )}

          <Separator />

          <div className="flex gap-2">
            <Button
              onClick={handleRun}
              disabled={loading}
              className="flex-1 gap-2"
            >
              {loading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Play className="h-4 w-4" />
              )}
              {loading ? "Running…" : "Run"}
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

          {(result || error) && (
            <ToolOutput
              result={error ? { type: "error", output: error } : result}
              outputType={schema.outputType}
              elapsedMs={elapsedMs}
              toolName={schema.toolName}
            />
          )}

          {!result && !error && !loading && schema.fields.length === 0 && (
            <div className="flex flex-col items-center gap-3 py-6 text-muted-foreground">
              <Wrench className="h-10 w-10 opacity-20" />
              <p className="text-xs">Result will appear here after running</p>
            </div>
          )}
        </div>
      </ScrollArea>

      {/* Tool reference info — collapsible bottom panel */}
      <ToolInfoPanel schema={schema} />
    </div>
  );
}
