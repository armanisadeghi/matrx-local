import { useState, useCallback, useEffect, useRef } from "react";
import {
  Globe, ArrowRight, Camera, Code2, RefreshCw, ExternalLink,
  MousePointer, Type, Layers, Play, Plus, Trash2, GripVertical,
  CheckCircle2, XCircle, Loader2, MonitorPlay, Circle,
} from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ToolSection } from "@/components/tools/shared/ToolSection";
import { OutputCard } from "@/components/tools/shared/OutputCard";
import { cn } from "@/lib/utils";

interface BrowserPanelProps {
  onInvoke: (toolName: string, params: Record<string, unknown>) => Promise<void>;
  loading: boolean;
  result: unknown;
}

function parseOutput(result: unknown): { text?: string; image?: string; type?: string } | null {
  try {
    const d = result as { output?: string; type?: string };
    if (!d) return null;
    if (d.type === "error") return { text: d?.output ?? "Error", type: "error" };
    if (d.output) {
      try {
        const j = JSON.parse(d.output);
        if (typeof j === "object" && j !== null) return j;
        return { text: d.output };
      } catch {
        return { text: d.output };
      }
    }
    return null;
  } catch { return null; }
}

interface TabEntry { id?: string; url?: string; title?: string; active?: boolean }

function tryParseTabs(result: unknown): TabEntry[] | null {
  try {
    const d = result as { output?: string; type?: string };
    if (!d || d.type === "error" || !d.output) return null;
    const arr = JSON.parse(d.output);
    if (Array.isArray(arr)) return arr;
    return null;
  } catch { return null; }
}

type StepType = "navigate" | "click" | "type" | "extract" | "screenshot" | "eval";
type StepStatus = "pending" | "running" | "done" | "error";

interface AutoStep {
  id: string;
  type: StepType;
  params: Record<string, string>;
  status: StepStatus;
  output?: string;
  screenshot?: string;
}

const STEP_DEFAULTS: Record<StepType, Record<string, string>> = {
  navigate:   { url: "https://" },
  click:      { selector: "" },
  type:       { selector: "", text: "" },
  extract:    { selector: "" },
  screenshot: {},
  eval:       { script: "document.title" },
};

const STEP_META: Record<StepType, { label: string; icon: React.ElementType; color: string }> = {
  navigate:   { label: "Navigate",   icon: Globe,        color: "text-cyan-400" },
  click:      { label: "Click",      icon: MousePointer, color: "text-violet-400" },
  type:       { label: "Type",       icon: Type,         color: "text-emerald-400" },
  extract:    { label: "Extract",    icon: ExternalLink,  color: "text-amber-400" },
  screenshot: { label: "Screenshot", icon: Camera,       color: "text-pink-400" },
  eval:       { label: "JavaScript", icon: Code2,        color: "text-orange-400" },
};

function stepToInvoke(step: AutoStep): { toolName: string; params: Record<string, unknown> } {
  switch (step.type) {
    case "navigate":   return { toolName: "BrowserNavigate",  params: { url: step.params.url } };
    case "click":      return { toolName: "BrowserClick",     params: { selector: step.params.selector } };
    case "type":       return { toolName: "BrowserType",      params: { selector: step.params.selector, text: step.params.text } };
    case "extract":    return { toolName: "BrowserExtract",   params: step.params.selector ? { selector: step.params.selector } : {} };
    case "screenshot": return { toolName: "BrowserScreenshot", params: {} };
    case "eval":       return { toolName: "BrowserEval",      params: { script: step.params.script } };
  }
}

export function BrowserPanel({ onInvoke, loading, result }: BrowserPanelProps) {
  const [view, setView] = useState<"automation" | "navigate" | "console">("automation");
  const [url, setUrl]           = useState("https://");
  const [screenshot, setScreenshot] = useState<string | null>(null);
  const [tabs, setTabs] = useState<TabEntry[]>([]);
  const [sessionActive, setSessionActive] = useState(false);
  const [steps, setSteps] = useState<AutoStep[]>([
    { id: "1", type: "navigate", params: { url: "https://" }, status: "pending" },
    { id: "2", type: "screenshot", params: {}, status: "pending" },
  ]);
  const [runningStepId, setRunningStepId] = useState<string | null>(null);
  const [autoScreenshot, setAutoScreenshot] = useState(true);
  const [evalCode, setEvalCode] = useState("document.title");
  const resultRef = useRef<unknown>(null);
  const resolveRef = useRef<((r: unknown) => void) | null>(null);

  const parsed = parseOutput(result);
  const parsedTabs = tryParseTabs(result);

  useEffect(() => {
    if (parsedTabs && parsedTabs.length > 0) setTabs(parsedTabs);
  }, [parsedTabs]);

  // Route result to the current waiting step resolver
  useEffect(() => {
    resultRef.current = result;
    if (resolveRef.current) {
      resolveRef.current(result);
      resolveRef.current = null;
    }
    if (parsed?.image) setScreenshot(parsed.image);
  }, [result, parsed?.image]);

  const invokeAndWait = useCallback((toolName: string, params: Record<string, unknown>): Promise<unknown> => {
    return new Promise((resolve) => {
      resolveRef.current = resolve;
      onInvoke(toolName, params);
    });
  }, [onInvoke]);

  const navigate = useCallback(() => {
    let target = url.trim();
    if (!target.startsWith("http")) target = "https://" + target;
    setUrl(target);
    setSessionActive(true);
    onInvoke("BrowserNavigate", { url: target });
  }, [onInvoke, url]);

  const takeScreenshot = useCallback(async () => {
    await onInvoke("BrowserScreenshot", {});
  }, [onInvoke]);

  // Run all steps in sequence
  const runAllSteps = useCallback(async () => {
    setSessionActive(true);
    for (const step of steps) {
      if (step.status === "done") continue;
      setRunningStepId(step.id);
      setSteps((prev) => prev.map((s) => s.id === step.id ? { ...s, status: "running" } : s));

      const { toolName, params } = stepToInvoke(step);
      const res = await invokeAndWait(toolName, params) as { output?: string; type?: string };

      const isError = res?.type === "error";
      let output = res?.output ?? "";
      let stepScreenshot: string | undefined;

      // Try to extract image from result
      try {
        const j = JSON.parse(output);
        if (j?.image) { stepScreenshot = j.image; output = "Screenshot captured"; }
      } catch { /* not JSON */ }

      setSteps((prev) => prev.map((s) =>
        s.id === step.id ? { ...s, status: isError ? "error" : "done", output, screenshot: stepScreenshot } : s
      ));

      if (isError) break;

      // Auto-screenshot after each non-screenshot step
      if (autoScreenshot && step.type !== "screenshot") {
        const ssRes = await invokeAndWait("BrowserScreenshot", {}) as { output?: string; type?: string };
        try {
          const j = JSON.parse(ssRes?.output ?? "");
          if (j?.image) setScreenshot(j.image);
        } catch { /* ignore */ }
      }
    }
    setRunningStepId(null);
  }, [steps, invokeAndWait, autoScreenshot]);

  const addStep = useCallback((type: StepType) => {
    const id = String(Date.now());
    setSteps((prev) => [...prev, { id, type, params: { ...STEP_DEFAULTS[type] }, status: "pending" }]);
  }, []);

  const removeStep = useCallback((id: string) => {
    setSteps((prev) => prev.filter((s) => s.id !== id));
  }, []);

  const updateStepParam = useCallback((id: string, key: string, value: string) => {
    setSteps((prev) => prev.map((s) => s.id === id ? { ...s, params: { ...s.params, [key]: value } } : s));
  }, []);

  const resetSteps = useCallback(() => {
    setSteps((prev) => prev.map((s) => ({ ...s, status: "pending", output: undefined, screenshot: undefined })));
  }, []);

  const textOut = parsed?.text;

  return (
    <div className="flex h-full flex-col gap-4 p-5 overflow-auto">
      {/* View switcher */}
      <div className="flex gap-1 rounded-xl border bg-muted/20 p-1">
        {([
          { key: "automation", label: "Automation", icon: MonitorPlay },
          { key: "navigate",   label: "Quick Nav",  icon: Globe },
          { key: "console",    label: "Console",    icon: Code2 },
        ] as const).map((v) => (
          <button key={v.key} onClick={() => setView(v.key)}
            className={cn(
              "flex-1 flex items-center justify-center gap-1.5 rounded-lg py-1.5 text-xs font-medium transition-all",
              view === v.key ? "bg-background shadow text-foreground" : "text-muted-foreground hover:text-foreground"
            )}>
            <v.icon className="h-3.5 w-3.5" />
            {v.label}
          </button>
        ))}
      </div>

      {/* Session indicator */}
      <div className="flex items-center gap-2">
        <div className={cn(
          "flex items-center gap-1.5 rounded-full px-2.5 py-1 border text-[11px] font-medium",
          sessionActive
            ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-400"
            : "border-border text-muted-foreground"
        )}>
          <Circle className={cn("h-2 w-2 fill-current", sessionActive ? "text-emerald-400" : "text-muted-foreground/40")} />
          {sessionActive ? "Session Active" : "No Session"}
        </div>
        {tabs.length > 0 && (
          <span className="text-[11px] text-muted-foreground">{tabs.length} tab{tabs.length !== 1 ? "s" : ""} open</span>
        )}
      </div>

      {/* ── AUTOMATION ── */}
      {view === "automation" && (
        <>
          {/* Step builder */}
          <ToolSection title="Automation Steps" icon={MonitorPlay} iconColor="text-cyan-400"
            actions={
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setAutoScreenshot((v) => !v)}
                  className={cn(
                    "text-[10px] px-2 py-0.5 rounded-full border font-medium transition-colors",
                    autoScreenshot
                      ? "border-pink-500/40 bg-pink-500/10 text-pink-400"
                      : "border-border text-muted-foreground"
                  )}
                  title="Auto-capture screenshot after each step"
                >
                  <Camera className="h-2.5 w-2.5 inline mr-1" />
                  Auto-SS
                </button>
                <button onClick={resetSteps}
                  className="text-[10px] px-2 py-0.5 rounded-full border border-border text-muted-foreground hover:text-foreground transition-colors">
                  Reset
                </button>
              </div>
            } noPadding>
            <div className="space-y-1 p-2">
              {steps.map((step, idx) => {
                const meta = STEP_META[step.type];
                const Icon = meta.icon;
                const isRunning = step.id === runningStepId;
                return (
                  <div key={step.id} className={cn(
                    "rounded-xl border transition-all",
                    isRunning ? "border-cyan-500/50 bg-cyan-500/5" : "border-border/60 bg-card/30",
                    step.status === "done" ? "border-emerald-500/30" : "",
                    step.status === "error" ? "border-red-500/30" : "",
                  )}>
                    <div className="flex items-center gap-2 px-3 py-2">
                      {/* Step number + drag handle */}
                      <GripVertical className="h-3.5 w-3.5 text-muted-foreground/30 shrink-0 cursor-grab" />
                      <span className="text-[10px] font-mono text-muted-foreground/50 w-4 shrink-0">{idx + 1}</span>
                      {/* Status icon */}
                      {isRunning ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin text-cyan-400 shrink-0" />
                      ) : step.status === "done" ? (
                        <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400 shrink-0" />
                      ) : step.status === "error" ? (
                        <XCircle className="h-3.5 w-3.5 text-red-400 shrink-0" />
                      ) : (
                        <Icon className={cn("h-3.5 w-3.5 shrink-0", meta.color)} />
                      )}
                      {/* Step type label */}
                      <span className={cn("text-xs font-medium w-20 shrink-0", meta.color)}>{meta.label}</span>
                      {/* Params */}
                      <div className="flex-1 flex gap-2 min-w-0">
                        {step.type === "navigate" && (
                          <Input value={step.params.url} onChange={(e) => updateStepParam(step.id, "url", e.target.value)}
                            placeholder="https://" className="h-7 text-[11px] font-mono" />
                        )}
                        {(step.type === "click" || step.type === "extract") && (
                          <Input value={step.params.selector} onChange={(e) => updateStepParam(step.id, "selector", e.target.value)}
                            placeholder="CSS selector" className="h-7 text-[11px] font-mono" />
                        )}
                        {step.type === "type" && (
                          <>
                            <Input value={step.params.selector} onChange={(e) => updateStepParam(step.id, "selector", e.target.value)}
                              placeholder="CSS selector" className="h-7 text-[11px] font-mono w-36 shrink-0" />
                            <Input value={step.params.text} onChange={(e) => updateStepParam(step.id, "text", e.target.value)}
                              placeholder="Text to type" className="h-7 text-[11px] flex-1" />
                          </>
                        )}
                        {step.type === "eval" && (
                          <Input value={step.params.script} onChange={(e) => updateStepParam(step.id, "script", e.target.value)}
                            placeholder="JavaScript expression" className="h-7 text-[11px] font-mono" />
                        )}
                        {step.type === "screenshot" && (
                          <span className="text-[11px] text-muted-foreground italic self-center">Capture page screenshot</span>
                        )}
                      </div>
                      <button onClick={() => removeStep(step.id)}
                        className="text-muted-foreground/40 hover:text-destructive transition-colors shrink-0 ml-1">
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                    {/* Step output */}
                    {(step.output || step.screenshot) && step.status !== "pending" && (
                      <div className={cn(
                        "mx-2 mb-2 rounded-lg px-3 py-1.5 text-[11px] font-mono",
                        step.status === "error" ? "bg-red-500/10 text-red-400" : "bg-muted/30 text-muted-foreground"
                      )}>
                        {step.screenshot ? (
                          <div className="rounded overflow-hidden">
                            <img src={`data:image/png;base64,${step.screenshot}`} alt="step screenshot" className="w-full max-h-40 object-cover" />
                          </div>
                        ) : (
                          <span className="line-clamp-2">{step.output}</span>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}

              {/* Add step buttons */}
              <div className="pt-2 flex flex-wrap gap-1.5">
                {(Object.keys(STEP_META) as StepType[]).map((t) => {
                  const meta = STEP_META[t];
                  const Icon = meta.icon;
                  return (
                    <button key={t} onClick={() => addStep(t)}
                      className={cn(
                        "flex items-center gap-1 rounded-lg border px-2 py-1 text-[11px] font-medium transition-colors",
                        "border-border text-muted-foreground hover:border-primary/40 hover:text-foreground hover:bg-muted/30"
                      )}>
                      <Plus className="h-3 w-3" />
                      <Icon className="h-3 w-3" />
                      {meta.label}
                    </button>
                  );
                })}
              </div>
            </div>
          </ToolSection>

          {/* Run button */}
          <Button
            onClick={runAllSteps}
            disabled={loading || runningStepId !== null || steps.length === 0}
            className="gap-2 w-full"
          >
            {runningStepId ? (
              <><Loader2 className="h-4 w-4 animate-spin" /> Running...</>
            ) : (
              <><Play className="h-4 w-4" /> Run All Steps</>
            )}
          </Button>

          {/* Live screenshot */}
          {screenshot && (
            <ToolSection title="Live Page View" icon={Camera} iconColor="text-pink-400"
              actions={
                <Button variant="ghost" size="icon" className="h-7 w-7" onClick={takeScreenshot} disabled={loading}>
                  <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
                </Button>
              }>
              <div className="rounded-lg overflow-hidden border">
                <img src={`data:image/png;base64,${screenshot}`} alt="Browser screenshot" className="w-full" />
              </div>
            </ToolSection>
          )}
        </>
      )}

      {/* ── QUICK NAV ── */}
      {view === "navigate" && (
        <>
          <ToolSection title="Browser Navigation" icon={Globe} iconColor="text-cyan-400">
            <div className="space-y-3">
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <Globe className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
                  <Input value={url} onChange={(e) => setUrl(e.target.value)}
                    placeholder="https://example.com"
                    className="pl-9 font-mono text-xs"
                    onKeyDown={(e) => { if (e.key === "Enter") navigate(); }}
                  />
                </div>
                <Button onClick={navigate} disabled={loading} className="gap-1.5 shrink-0">
                  {loading ? <RefreshCw className="h-4 w-4 animate-spin" /> : <ArrowRight className="h-4 w-4" />}
                  Go
                </Button>
              </div>

              <div className="flex gap-2">
                <Button variant="outline" size="sm" onClick={takeScreenshot} disabled={loading} className="gap-1.5 flex-1">
                  <Camera className="h-3.5 w-3.5" /> Screenshot
                </Button>
                <Button variant="outline" size="sm"
                  onClick={() => onInvoke("BrowserTabs", {})} disabled={loading} className="gap-1.5 flex-1">
                  <Layers className="h-3.5 w-3.5" /> Tabs
                </Button>
              </div>
            </div>
          </ToolSection>

          {screenshot && (
            <ToolSection title="Screenshot" icon={Camera} iconColor="text-cyan-400">
              <div className="rounded-lg overflow-hidden border">
                <img src={`data:image/png;base64,${screenshot}`} alt="Browser screenshot" className="w-full" />
              </div>
            </ToolSection>
          )}

          {tabs.length > 0 && (
            <ToolSection title="Open Tabs" icon={Layers} iconColor="text-cyan-400" noPadding>
              <div className="divide-y divide-border/30 max-h-48 overflow-auto">
                {tabs.map((t, i) => (
                  <div key={t.id ?? i} className="flex items-center gap-3 px-4 py-2 hover:bg-muted/20 transition-colors">
                    <Globe className="h-3.5 w-3.5 text-cyan-400 shrink-0" />
                    <div className="min-w-0 flex-1">
                      <p className="text-xs font-medium truncate">{t.title ?? "Untitled"}</p>
                      <p className="text-[10px] text-muted-foreground font-mono truncate">{t.url}</p>
                    </div>
                    {t.active && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded-full border border-cyan-500/30 text-cyan-400 bg-cyan-500/10">Active</span>
                    )}
                  </div>
                ))}
              </div>
            </ToolSection>
          )}

          {textOut && !screenshot && !parsedTabs && <OutputCard title="Result" content={textOut} />}
        </>
      )}

      {/* ── CONSOLE ── */}
      {view === "console" && (
        <>
          <ToolSection title="JavaScript Console" icon={Code2} iconColor="text-cyan-400">
            <div className="space-y-3">
              <Textarea value={evalCode} onChange={(e) => setEvalCode(e.target.value)}
                rows={6} className="dark resize-none font-mono text-xs bg-zinc-950/80 text-emerald-400 border-zinc-700/50"
                placeholder="// JavaScript to evaluate in page context..." />
              <div className="flex gap-2">
                <div className="flex flex-wrap gap-1.5 flex-1">
                  {["document.title", "document.URL", "document.querySelectorAll('a').length"].map((snippet) => (
                    <button key={snippet} onClick={() => setEvalCode(snippet)}
                      className="text-[10px] px-2 py-1 rounded-md border bg-muted/30 hover:bg-muted/60 transition-colors font-mono truncate max-w-[150px]">
                      {snippet}
                    </button>
                  ))}
                </div>
                <Button onClick={() => onInvoke("BrowserEval", { script: evalCode })}
                  disabled={loading || !evalCode} className="shrink-0 gap-1.5">
                  <Play className="h-3.5 w-3.5" /> Run
                </Button>
              </div>
            </div>
          </ToolSection>

          {textOut && (
            <OutputCard title="Console Output" content={textOut} format="code" maxHeight={300} />
          )}
        </>
      )}
    </div>
  );
}
