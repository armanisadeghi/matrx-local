import { useState, useCallback, useRef } from "react";
import { Terminal, Play, Square, FileCode, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ToolSection } from "@/components/tools/shared/ToolSection";
import { OutputCard } from "@/components/tools/shared/OutputCard";
import type { ToolUISchema } from "@/types/tool-schema";
import { cn } from "@/lib/utils";

interface TerminalPanelProps {
  onInvoke: (toolName: string, params: Record<string, unknown>) => Promise<void>;
  loading: boolean;
  result: unknown;
  tools?: ToolUISchema[];
}

function parseOutput(result: unknown): { text: string; error?: boolean; exitCode?: number } | null {
  try {
    const d = result as { output?: string; type?: string; metadata?: Record<string, unknown> };
    if (!d) return null;
    const isError = d.type === "error";
    return {
      text: d.output ?? "",
      error: isError,
      exitCode: d.metadata?.exit_code as number | undefined,
    };
  } catch { return null; }
}

interface CommandHistoryEntry {
  command: string;
  output: string;
  error: boolean;
  timestamp: Date;
}

export function TerminalPanel({ onInvoke, loading, result }: TerminalPanelProps) {
  const [view, setView] = useState<"shell" | "scripts">("shell");
  const [command, setCommand] = useState("");
  const [commandHistory, setCommandHistory] = useState<CommandHistoryEntry[]>([]);
  const [historyIndex, setHistoryIndex] = useState(-1);
  const [script, setScript] = useState("");
  const [scriptType, setScriptType] = useState<"applescript" | "powershell">("applescript");
  const [bgTaskId, setBgTaskId] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const output = parseOutput(result);

  const runCommand = useCallback(async () => {
    if (!command.trim()) return;
    const cmd = command.trim();
    setCommand("");
    setHistoryIndex(-1);
    await onInvoke("Bash", { command: cmd });

    // Add to history after result is back
    const out = parseOutput(result);
    setCommandHistory((h) => [{
      command: cmd,
      output: out?.text ?? "",
      error: out?.error ?? false,
      timestamp: new Date(),
    }, ...h.slice(0, 49)]);
  }, [command, onInvoke, result]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      runCommand();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      const commands = commandHistory.map((h) => h.command);
      const next = Math.min(historyIndex + 1, commands.length - 1);
      setHistoryIndex(next);
      if (commands[next]) setCommand(commands[next]);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      const commands = commandHistory.map((h) => h.command);
      const next = Math.max(historyIndex - 1, -1);
      setHistoryIndex(next);
      setCommand(next >= 0 ? commands[next] : "");
    }
  }, [runCommand, commandHistory, historyIndex]);

  const runScript = useCallback(async () => {
    if (!script.trim()) return;
    const tool = scriptType === "applescript" ? "AppleScript" : "PowerShellScript";
    await onInvoke(tool, { script });
  }, [script, scriptType, onInvoke]);

  return (
    <div className="flex h-full flex-col gap-4 p-5 overflow-auto">
      {/* View switcher */}
      <div className="flex gap-1 rounded-xl border bg-muted/20 p-1">
        {([
          { key: "shell", label: "Shell", icon: Terminal },
          { key: "scripts", label: "Scripts", icon: FileCode },
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

      {/* ── SHELL ── */}
      {view === "shell" && (
        <>
          {/* Command input */}
          <div className="dark rounded-2xl border border-zinc-700/50 bg-zinc-900/90 overflow-hidden text-zinc-200">
            <div className="flex items-center gap-2 px-4 py-2.5 border-b border-zinc-700/50">
              <div className="flex gap-1.5">
                <div className="h-2.5 w-2.5 rounded-full bg-red-500/80" />
                <div className="h-2.5 w-2.5 rounded-full bg-yellow-500/80" />
                <div className="h-2.5 w-2.5 rounded-full bg-green-500/80" />
              </div>
              <span className="text-[10px] text-zinc-500 font-mono">bash</span>
            </div>

            {/* Output area */}
            {output?.text && (
              <div className="max-h-64 overflow-auto px-4 py-3">
                <pre className={cn(
                  "whitespace-pre-wrap break-words text-xs font-mono",
                  output.error ? "text-red-400" : "text-zinc-300"
                )}>
                  {output.text}
                </pre>
                {output.exitCode != null && output.exitCode !== 0 && (
                  <p className="text-[10px] text-red-400 mt-1 font-mono">Exit code: {output.exitCode}</p>
                )}
              </div>
            )}

            {/* Input line */}
            <div className="flex items-center gap-2 px-4 py-2 border-t border-zinc-700/50">
              <span className="text-emerald-400 text-xs font-mono">$</span>
              <input
                ref={inputRef}
                value={command}
                onChange={(e) => setCommand(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Type a command..."
                className="flex-1 bg-transparent text-xs text-zinc-200 font-mono placeholder:text-zinc-600 outline-none"
                autoFocus
              />
              <Button size="sm" variant="ghost" onClick={runCommand} disabled={loading || !command.trim()}
                className="h-6 w-6 p-0 text-zinc-400 hover:text-zinc-200">
                {loading ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
              </Button>
            </div>
          </div>

          {/* Background tasks */}
          <ToolSection title="Background Tasks" icon={Terminal} iconColor="text-muted-foreground">
            <div className="space-y-2">
              <div className="flex gap-2">
                <Input value={command} onChange={(e) => setCommand(e.target.value)}
                  placeholder="Command to run in background" className="text-xs font-mono flex-1" />
                <Button size="sm" variant="outline" className="gap-1 shrink-0"
                  onClick={async () => {
                    await onInvoke("Bash", { command, run_in_background: true });
                    // Try to extract task ID
                    try {
                      const d = result as { metadata?: Record<string, unknown> };
                      if (d?.metadata?.task_id) setBgTaskId(String(d.metadata.task_id));
                    } catch { /* ignore */ }
                  }} disabled={loading || !command.trim()}>
                  <Play className="h-3.5 w-3.5" /> Background
                </Button>
              </div>
              {bgTaskId && (
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-muted-foreground font-mono">Task: {bgTaskId}</span>
                  <Button size="sm" variant="ghost" className="h-6 text-[10px]"
                    onClick={() => onInvoke("BashOutput", { bash_id: bgTaskId })} disabled={loading}>
                    Get Output
                  </Button>
                  <Button size="sm" variant="ghost" className="h-6 text-[10px] text-destructive"
                    onClick={() => { onInvoke("TaskStop", { task_id: bgTaskId }); setBgTaskId(null); }} disabled={loading}>
                    <Square className="h-3 w-3 mr-1" /> Stop
                  </Button>
                </div>
              )}
            </div>
          </ToolSection>

          {/* Command history */}
          {commandHistory.length > 0 && (
            <ToolSection title="History" icon={Terminal} iconColor="text-muted-foreground" noPadding>
              <div className="divide-y divide-border/30 max-h-48 overflow-auto">
                {commandHistory.slice(0, 10).map((entry, i) => (
                  <button key={i} onClick={() => { setCommand(entry.command); inputRef.current?.focus(); }}
                    className="w-full text-left px-4 py-2 hover:bg-muted/20 transition-colors">
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] text-emerald-400 font-mono">$</span>
                      <span className="text-xs font-mono truncate flex-1">{entry.command}</span>
                      <span className="text-[10px] text-muted-foreground">{entry.timestamp.toLocaleTimeString()}</span>
                    </div>
                  </button>
                ))}
              </div>
            </ToolSection>
          )}
        </>
      )}

      {/* ── SCRIPTS ── */}
      {view === "scripts" && (
        <>
          <ToolSection title="Run Script" icon={FileCode} iconColor="text-muted-foreground">
            <div className="space-y-3">
              <div className="flex gap-1 rounded-lg border bg-muted/20 p-0.5">
                {(["applescript", "powershell"] as const).map((t) => (
                  <button key={t} onClick={() => setScriptType(t)}
                    className={cn(
                      "flex-1 rounded-md px-3 py-1 text-xs font-medium transition-all capitalize",
                      scriptType === t ? "bg-background shadow text-foreground" : "text-muted-foreground"
                    )}>
                    {t === "applescript" ? "AppleScript" : "PowerShell"}
                  </button>
                ))}
              </div>
              <Textarea
                value={script}
                onChange={(e) => setScript(e.target.value)}
                placeholder={scriptType === "applescript"
                  ? 'tell application "Finder" to activate'
                  : 'Get-Process | Sort-Object CPU -Descending | Select-Object -First 10'
                }
                rows={8}
                className="font-mono text-xs resize-none"
              />
              <Button size="sm" className="w-full gap-1.5" onClick={runScript}
                disabled={loading || !script.trim()}>
                <Play className="h-3.5 w-3.5" />
                {loading ? "Running..." : "Run Script"}
              </Button>
            </div>
          </ToolSection>

          {output && <OutputCard title="Script Output" content={output.text} status={output.error ? "error" : "success"} />}
        </>
      )}
    </div>
  );
}
