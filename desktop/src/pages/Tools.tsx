import { useState, useMemo, useEffect, useCallback } from "react";
import { Search, Code2, History, X, ChevronRight, Bot } from "lucide-react";
import * as LucideIcons from "lucide-react";
import type { LucideProps } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ToolDetailPanel } from "@/components/tools/ToolDetailPanel";
import { MonitoringPanel } from "@/components/tools/panels/MonitoringPanel";
import { GenericToolPanel } from "@/components/tools/panels/GenericToolPanel";
import { useToolExecution } from "@/hooks/use-tool-execution";
import {
  fromEngineSchema,
  toolCategories,
  toolSchemas,
  categoryColorMap,
  getCategoryMeta,
} from "@/lib/tool-registry";
import type { EngineStatus } from "@/hooks/use-engine";
import type { ToolUISchema } from "@/types/tool-schema";
import { cn } from "@/lib/utils";

interface ToolsProps {
  engineStatus: EngineStatus;
  engineUrl: string | null;
  tools: string[];
}

interface EngineToolSchema {
  name: string;
  description: string;
  category?: string;
  input_schema?: {
    properties?: Record<
      string,
      { type?: string; description?: string; default?: unknown }
    >;
    required?: string[];
  };
}

function DynamicIcon({ name, ...props }: { name: string } & LucideProps) {
  const key = name
    .split("-")
    .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
    .join("");
  // @ts-expect-error dynamic lucide icon
  const Icon = LucideIcons[key] as React.ComponentType<LucideProps> | undefined;
  if (!Icon) return <LucideIcons.Wrench {...props} />;
  return <Icon {...props} />;
}

// Tools with special rich dashboards that are genuinely better than a form
const RICH_DASHBOARD_TOOLS = new Set(["SystemResources", "TopProcesses"]);

function ConsumerPanel({
  schema,
  onInvoke,
  loading,
  result,
  error,
  elapsedMs,
  onReset,
}: {
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
}) {
  // SystemResources and TopProcesses have a live-gauge dashboard that's genuinely richer
  if (RICH_DASHBOARD_TOOLS.has(schema.toolName)) {
    return (
      <MonitoringPanel onInvoke={onInvoke} loading={loading} result={result} />
    );
  }

  // Every other tool gets its own form-based view driven by the schema
  return (
    <GenericToolPanel
      schema={schema}
      onInvoke={onInvoke}
      loading={loading}
      result={result}
      error={error}
      elapsedMs={elapsedMs}
      onReset={onReset}
    />
  );
}

export function Tools({ engineStatus, engineUrl, tools }: ToolsProps) {
  const [search, setSearch] = useState("");
  const [selectedCategory, setSelectedCategory] = useState<string>("");
  const [selectedToolName, setSelectedToolName] = useState<string | null>(null);
  const [engineSchemas, setEngineSchemas] = useState<ToolUISchema[]>([]);
  const [advancedMode, setAdvancedMode] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);

  const { loading, result, error, elapsedMs, history, invoke, reset } =
    useToolExecution();

  useEffect(() => {
    if (engineStatus !== "connected" || !engineUrl) return;
    const load = async () => {
      try {
        const resp = await fetch(`${engineUrl}/chat/tools`);
        if (!resp.ok) return;
        const data = await resp.json();
        const schemas = ((data.tools ?? []) as EngineToolSchema[]).map(
          fromEngineSchema,
        );
        setEngineSchemas(schemas);
        const firstSchema = schemas[0];
        if (!selectedToolName && firstSchema)
          setSelectedToolName(firstSchema.toolName);
      } catch {
        /* graceful fallback */
      }
    };
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [engineStatus, engineUrl]);

  const schemas = useMemo(() => {
    if (engineSchemas.length > 0) return engineSchemas;
    if (tools.length > 0)
      return tools
        .map((t) => toolSchemas.find((s) => s.toolName === t))
        .filter((s): s is ToolUISchema => s != null);
    return toolSchemas;
  }, [tools, engineSchemas]);

  const categoryList = useMemo(() => {
    const counts = new Map<string, number>();
    for (const s of schemas) {
      counts.set(s.category, (counts.get(s.category) ?? 0) + 1);
    }
    const ordered = toolCategories
      .filter((c) => counts.has(c.id))
      .map((c) => ({ ...c, count: counts.get(c.id)! }));
    const extra = Array.from(counts.entries())
      .filter(([id]) => !toolCategories.find((c) => c.id === id))
      .map(([id, count]) => ({
        id,
        label: id,
        description: "",
        icon: "wrench",
        color: "slate",
        panelType: "generic" as const,
        count,
      }));
    return [...ordered, ...extra];
  }, [schemas]);

  useEffect(() => {
    const firstCategory = categoryList[0];
    if (!selectedCategory && firstCategory) {
      setSelectedCategory(firstCategory.id);
    }
  }, [categoryList, selectedCategory]);

  const filteredSchemas = useMemo(() => {
    if (!search) return schemas.filter((s) => s.category === selectedCategory);
    const q = search.toLowerCase();
    return schemas.filter(
      (s) =>
        s.displayName.toLowerCase().includes(q) ||
        s.toolName.toLowerCase().includes(q) ||
        s.description.toLowerCase().includes(q),
    );
  }, [schemas, selectedCategory, search]);

  const selectedSchema = selectedToolName
    ? (schemas.find((s) => s.toolName === selectedToolName) ?? null)
    : null;

  const handleCategorySelect = useCallback(
    (catId: string) => {
      setSelectedCategory(catId);
      setSearch("");
      reset();
      const first = schemas.find((s) => s.category === catId);
      if (first) setSelectedToolName(first.toolName);
    },
    [schemas, reset],
  );

  const handleToolSelect = useCallback(
    (schema: ToolUISchema) => {
      setSelectedToolName(schema.toolName);
      reset();
      setAdvancedMode(false);
    },
    [reset],
  );

  const invokeForPanel = useCallback(
    async (toolName: string, params: Record<string, unknown>) => {
      await invoke(toolName, params);
    },
    [invoke],
  );

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PageHeader
        title="Tools"
        description={`${schemas.length} tools available`}
      />

      <div className="flex flex-1 overflow-hidden">
        {/* ===== LEFT: Unified sidebar ===== */}
        <div className="flex w-[240px] shrink-0 flex-col border-r bg-sidebar">
          <div className="p-3 border-b">
            <div className="relative">
              <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
              <Input
                placeholder="Search tools..."
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value);
                  if (e.target.value) setSelectedToolName(null);
                }}
                className="h-8 pl-8 text-xs bg-background/50"
              />
            </div>
          </div>

          <ScrollArea className="flex-1">
            <div className="p-2 space-y-0.5">
              {!search &&
                categoryList.map((cat) => {
                  const colors =
                    categoryColorMap[cat.color] ?? categoryColorMap["slate"]!;
                  const isActive = selectedCategory === cat.id;
                  const catTools = schemas.filter((s) => s.category === cat.id);

                  return (
                    <div key={cat.id}>
                      <button
                        onClick={() => handleCategorySelect(cat.id)}
                        className={cn(
                          "w-full flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition-all group",
                          isActive
                            ? `${colors.bg} ${colors.border} border`
                            : "border border-transparent hover:bg-muted/40",
                        )}
                      >
                        <div
                          className={cn(
                            "h-6 w-6 shrink-0 rounded-md flex items-center justify-center transition-colors",
                            isActive
                              ? colors.text
                              : "text-muted-foreground group-hover:text-foreground",
                          )}
                        >
                          <DynamicIcon
                            name={cat.icon}
                            className="h-3.5 w-3.5"
                          />
                        </div>
                        <span
                          className={cn(
                            "flex-1 text-xs font-medium truncate",
                            isActive
                              ? "text-foreground"
                              : "text-muted-foreground group-hover:text-foreground",
                          )}
                        >
                          {cat.label}
                        </span>
                        <Badge
                          variant="secondary"
                          className="text-[10px] h-4 px-1.5 shrink-0 tabular-nums"
                        >
                          {cat.count}
                        </Badge>
                      </button>

                      {isActive && catTools.length > 0 && (
                        <div className="ml-4 mt-0.5 mb-1 space-y-px border-l border-border/40 pl-2">
                          {catTools.map((s) => {
                            const isToolActive =
                              selectedToolName === s.toolName;
                            return (
                              <button
                                key={s.toolName}
                                onClick={() => handleToolSelect(s)}
                                className={cn(
                                  "w-full text-left rounded-md px-2 py-1.5 transition-all text-[11px]",
                                  isToolActive
                                    ? `${colors.text} font-medium bg-background/60`
                                    : "text-muted-foreground hover:text-foreground hover:bg-muted/30",
                                )}
                              >
                                <div className="flex items-center gap-1.5">
                                  <span className="truncate flex-1">
                                    {s.displayName}
                                  </span>
                                  {isToolActive && (
                                    <ChevronRight className="h-2.5 w-2.5 shrink-0 opacity-50" />
                                  )}
                                </div>
                              </button>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  );
                })}

              {search &&
                filteredSchemas.map((s) => {
                  const isActive = selectedToolName === s.toolName;
                  const meta = getCategoryMeta(s.category);
                  const colors =
                    categoryColorMap[meta.color] ?? categoryColorMap["slate"]!;
                  return (
                    <button
                      key={s.toolName}
                      onClick={() => handleToolSelect(s)}
                      className={cn(
                        "w-full text-left rounded-lg px-2.5 py-2 transition-all border",
                        isActive
                          ? `${colors.bg} ${colors.border}`
                          : "border-transparent hover:bg-muted/40",
                      )}
                    >
                      <p className="text-xs font-medium truncate">
                        {s.displayName}
                      </p>
                      <p className="text-[10px] text-muted-foreground truncate">
                        {meta.label}
                      </p>
                    </button>
                  );
                })}
            </div>
          </ScrollArea>

          <div className="border-t p-3">
            <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
              <Bot className="h-3.5 w-3.5 text-primary" />
              <span>{schemas.length} tools available to AI</span>
            </div>
          </div>
        </div>

        {/* ===== RIGHT PANEL ===== */}
        <div className="flex flex-1 flex-col overflow-hidden">
          {selectedSchema ? (
            <>
              <div className="flex items-center justify-between border-b px-4 py-2.5 bg-card/30">
                <div className="min-w-0">
                  <h2 className="text-sm font-semibold text-foreground truncate">
                    {selectedSchema.displayName}
                  </h2>
                  <p className="text-[11px] text-muted-foreground truncate max-w-md">
                    {selectedSchema.description}
                  </p>
                </div>
                <div className="flex items-center gap-1.5 ml-4 shrink-0">
                  <Badge variant="secondary" className="text-[10px]">
                    {getCategoryMeta(selectedSchema.category).label}
                  </Badge>
                  <Button
                    variant={advancedMode ? "secondary" : "ghost"}
                    size="sm"
                    className={cn(
                      "h-7 gap-1 text-xs",
                      advancedMode && "bg-muted",
                    )}
                    onClick={() => setAdvancedMode((v) => !v)}
                  >
                    <Code2 className="h-3.5 w-3.5" />
                    {advancedMode ? "Simple" : "Advanced"}
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7"
                    onClick={() => setHistoryOpen((v) => !v)}
                  >
                    <History className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>

              <div className="flex flex-1 overflow-hidden">
                <div className="flex-1 overflow-hidden">
                  {advancedMode ? (
                    <ToolDetailPanel
                      schema={selectedSchema}
                      loading={loading}
                      result={result}
                      error={error}
                      elapsedMs={elapsedMs}
                      onInvoke={invoke}
                      onReset={reset}
                    />
                  ) : (
                    <div className="h-full overflow-auto">
                      <ConsumerPanel
                        schema={selectedSchema}
                        onInvoke={invokeForPanel}
                        loading={loading}
                        result={result}
                        error={error}
                        elapsedMs={elapsedMs}
                        onReset={reset}
                      />
                    </div>
                  )}
                </div>

                {historyOpen && (
                  <div className="w-[260px] shrink-0 border-l flex flex-col bg-card/30">
                    <div className="flex items-center justify-between border-b px-3 py-2">
                      <div className="flex items-center gap-2">
                        <History className="h-3.5 w-3.5 text-muted-foreground" />
                        <span className="text-xs font-semibold">History</span>
                      </div>
                      <button
                        onClick={() => setHistoryOpen(false)}
                        className="text-muted-foreground hover:text-foreground"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </div>
                    <ScrollArea className="flex-1">
                      <div className="p-2 space-y-1">
                        {history.length === 0 && (
                          <p className="text-xs text-muted-foreground text-center py-6">
                            No invocations yet
                          </p>
                        )}
                        {history.map((entry) => (
                          <div
                            key={entry.id}
                            className="rounded-lg border bg-card/50 px-2.5 py-2 space-y-1"
                          >
                            <div className="flex items-center gap-1.5">
                              <div
                                className={cn(
                                  "h-1.5 w-1.5 rounded-full shrink-0",
                                  entry.status === "success"
                                    ? "bg-emerald-400"
                                    : entry.status === "error"
                                      ? "bg-destructive"
                                      : "bg-amber-400 animate-pulse",
                                )}
                              />
                              <span className="text-xs font-medium truncate">
                                {entry.toolName}
                              </span>
                            </div>
                            <div className="flex items-center justify-between">
                              <span className="text-[10px] text-muted-foreground">
                                {entry.startedAt.toLocaleTimeString()}
                              </span>
                              {entry.elapsedMs != null && (
                                <span className="text-[10px] text-muted-foreground tabular-nums">
                                  {entry.elapsedMs < 1000
                                    ? `${entry.elapsedMs}ms`
                                    : `${(entry.elapsedMs / 1000).toFixed(1)}s`}
                                </span>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    </ScrollArea>
                  </div>
                )}
              </div>
            </>
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-4 text-muted-foreground">
              <div className="rounded-3xl bg-primary/5 border border-primary/10 p-6">
                <Bot className="h-12 w-12 text-primary/40" />
              </div>
              <div className="text-center">
                <p className="text-sm font-medium text-foreground">
                  Select a category
                </p>
                <p className="mt-1 text-xs max-w-xs">
                  {schemas.length} tools organized into {categoryList.length}{" "}
                  categories
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
