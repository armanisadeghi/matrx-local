import { useState, useRef, useCallback, useEffect } from "react";
import { ArrowUp, Square, Plus, ChevronDown, Cpu, FileText, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { LOCAL_MODEL_PREFIX, type ChatMode } from "@/hooks/use-chat";
import type { AgentInfo } from "@/types/agents";
import { AgentPicker } from "./AgentPicker";

interface ChatInputProps {
  onSend: (message: string) => void | Promise<void>;
  onStop: () => void;
  isStreaming: boolean;
  mode: ChatMode;
  model: string;
  availableModels: {
    id: string;
    label: string;
    provider?: string;
    default?: boolean;
  }[];
  onModelChange: (model: string) => void;
  onModeChange: (mode: ChatMode) => void;
  /** When true, the send button is disabled but the textarea remains typeable. */
  engineReady?: boolean;
  /** When true, focus the textarea on mount. */
  autoFocus?: boolean;
  // Agent props
  agents?: AgentInfo[];
  selectedAgentId?: string | null;
  onAgentChange?: (agentId: string | null) => void;
  agentsLoading?: boolean;
  showModelSelector?: boolean;
  showModeSelector?: boolean;
  /**
   * Optional replacement for the dead "+" stub — Cloud Chat passes its
   * fully-wired plus menu here; local chat keeps the stub untouched.
   */
  plusMenuSlot?: React.ReactNode;
  /** Files attached via the plus menu, shown as removable chips. */
  attachments?: Array<{ id: string; name: string; size: number }>;
  onRemoveAttachment?: (id: string) => void;
}

function formatAttachmentSize(size: number): string {
  if (size >= 1024) return `${Math.round(size / 1024)} KB`;
  return `${size} B`;
}

const modeLabels: Record<ChatMode, string> = {
  chat: "Chat",
  "co-work": "Co-Work",
  code: "Code",
};

export function ChatInput({
  onSend,
  onStop,
  isStreaming,
  mode,
  model,
  availableModels,
  onModelChange,
  onModeChange,
  engineReady = true,
  autoFocus = false,
  agents = [],
  selectedAgentId = null,
  onAgentChange,
  agentsLoading = false,
  showModelSelector = true,
  showModeSelector = true,
  plusMenuSlot,
  attachments = [],
  onRemoveAttachment,
}: ChatInputProps) {
  const [value, setValue] = useState("");
  const [showModelDropdown, setShowModelDropdown] = useState(false);
  const [showAgentPicker, setShowAgentPicker] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (autoFocus) {
      setTimeout(() => textareaRef.current?.focus(), 100);
    }
  }, [autoFocus]);

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value]);

  // Close model dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target as Node)
      ) {
        setShowModelDropdown(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const hasText = value.trim().length > 0;
  const canSend =
    (hasText || !!selectedAgentId) &&
    !isStreaming &&
    engineReady &&
    (!!model || !!selectedAgentId);

  const handleSend = useCallback(() => {
    if (!canSend) return;
    onSend(value);
    setValue("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [canSend, value, onSend]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  const selectedModel = availableModels.find((m) => m.id === model);
  const isLocalModel = model.startsWith(LOCAL_MODEL_PREFIX);

  return (
    <div className="mx-auto w-full max-w-3xl px-2 pt-1 md:px-4">
      {showModeSelector && (
        <div className="mb-1.5 flex items-center justify-center gap-1">
          {(Object.keys(modeLabels) as ChatMode[]).map((m) => (
            <button
              key={m}
              onClick={() => onModeChange(m)}
              className={cn(
                "rounded-full px-3 py-1 text-xs font-medium transition-all duration-200",
                mode === m
                  ? "bg-primary text-primary-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted",
              )}
            >
              {modeLabels[m]}
            </button>
          ))}
        </div>
      )}

      {/* Composer Container */}
      <div className="glass relative rounded-xl transition-shadow focus-within:shadow-md">
        {/* Attached-file chips */}
        {attachments.length > 0 && (
          <div className="flex flex-wrap gap-1.5 px-3 pt-2">
            {attachments.map((file) => (
              <span
                key={file.id}
                className="inline-flex max-w-[220px] items-center gap-1 rounded-md border border-border/60 bg-muted/30 px-1.5 py-0.5 text-[11px] text-foreground"
              >
                <FileText className="h-3 w-3 shrink-0 text-muted-foreground" />
                <span className="truncate">{file.name}</span>
                <span className="shrink-0 text-[10px] text-muted-foreground">
                  {formatAttachmentSize(file.size)}
                </span>
                {onRemoveAttachment && (
                  <button
                    type="button"
                    onClick={() => onRemoveAttachment(file.id)}
                    title={`Remove ${file.name}`}
                    className="ml-0.5 shrink-0 rounded text-muted-foreground transition-colors hover:text-foreground"
                  >
                    <X className="h-3 w-3" />
                  </button>
                )}
              </span>
            ))}
          </div>
        )}

        {/* Textarea */}
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            !engineReady
              ? "Waiting for engine..."
              : mode === "code"
                ? "Write or ask about code..."
                : mode === "co-work"
                  ? "What would you like to work on together?"
                  : "Message AI Matrx..."
          }
          rows={1}
          className="min-h-[2.5rem] w-full resize-none bg-transparent px-3 py-2 text-[0.9375rem] leading-relaxed text-foreground placeholder:text-muted-foreground focus:outline-none"
        />

        {/* Bottom bar */}
        <div className="flex items-center justify-between px-2 pb-1.5">
          {/* Left: Attach + Agent + Model */}
          <div className="flex items-center gap-1">
            {/* Attach / plus button — replaced by the wired menu when provided */}
            {plusMenuSlot ?? (
              <button
                className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:text-foreground"
                title="Attach files"
              >
                <Plus className="h-3.5 w-3.5" />
              </button>
            )}

            {/* Agent selector */}
            {onAgentChange && (
              <div className="relative">
                <button
                  type="button"
                  onClick={() => setShowAgentPicker(true)}
                  className={cn(
                    "flex max-w-[min(280px,40vw)] items-center gap-2 rounded-lg border border-border/60 bg-muted/20 px-3 py-1.5 text-xs transition-colors",
                    selectedAgentId
                      ? "border-primary/30 text-primary hover:bg-primary/5"
                      : "text-muted-foreground hover:border-border hover:bg-muted/40 hover:text-foreground",
                  )}
                >
                  <span className="truncate font-medium">
                    {selectedAgentId
                      ? (agents.find((a) => a.id === selectedAgentId)?.name ??
                        "Agent")
                      : "Choose agent"}
                  </span>
                  <ChevronDown className="h-3.5 w-3.5 shrink-0 opacity-60" />
                </button>

                <AgentPicker
                  agents={agents}
                  selectedAgentId={selectedAgentId}
                  onSelect={(id) => {
                    onAgentChange(id);
                  }}
                  isLoading={agentsLoading}
                  open={showAgentPicker}
                  onClose={() => setShowAgentPicker(false)}
                />
              </div>
            )}

            {/* Model selector */}
            {showModelSelector && (
              <div className="relative" ref={dropdownRef}>
                <button
                  onClick={() => setShowModelDropdown(!showModelDropdown)}
                  className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
                >
                  {isLocalModel && (
                    <Cpu className="h-3 w-3 text-blue-500 shrink-0" />
                  )}
                  <span className="max-w-[140px] truncate">
                    {selectedModel?.label ?? "Select model"}
                  </span>
                  <ChevronDown className="h-3 w-3" />
                </button>

                {showModelDropdown && (
                  <div className="glass absolute bottom-full left-0 mb-1.5 min-w-[260px] max-h-80 overflow-y-auto rounded-lg p-1.5">
                    {/* Empty state — engine not connected or models not yet synced */}
                    {availableModels.length === 0 && (
                      <div className="px-3 py-4 text-center text-[11px] text-muted-foreground">
                        No models available.
                        <br />
                        Connect to engine to load models.
                      </div>
                    )}
                    {/* Confidential Chat (on-device) models — shown first if any exist */}
                    {availableModels.some((m) => m.provider === "local") && (
                      <div>
                        <div className="flex items-center gap-1.5 px-3 pt-2 pb-1">
                          <Cpu className="h-3 w-3 text-blue-500" />
                          <span className="text-[10px] font-semibold uppercase tracking-wider text-blue-500/80">
                            Confidential Chat
                          </span>
                          <span className="ml-1 rounded-full bg-green-500/20 px-1.5 py-0.5 text-[9px] font-medium text-green-600">
                            On-device
                          </span>
                        </div>
                        {availableModels
                          .filter((m) => m.provider === "local")
                          .map((m) => (
                            <button
                              key={m.id}
                              onClick={() => {
                                onModelChange(m.id);
                                setShowModelDropdown(false);
                              }}
                              className={cn(
                                "flex w-full items-center rounded-md px-3 py-2 text-left text-xs transition-colors",
                                model === m.id
                                  ? "bg-accent text-accent-foreground"
                                  : "text-foreground hover:bg-accent/50",
                              )}
                            >
                              <Cpu className="h-3 w-3 text-blue-500 mr-2 shrink-0" />
                              <span className="font-medium">{m.label}</span>
                              <span className="ml-auto text-[10px] text-blue-500">
                                local
                              </span>
                            </button>
                          ))}
                        {availableModels.some(
                          (m) => m.provider !== "local",
                        ) && (
                          <div className="my-1.5 border-t border-border/50" />
                        )}
                      </div>
                    )}

                    {/* Cloud Models grouped by provider */}
                    {Object.entries(
                      availableModels
                        .filter((m) => m.provider !== "local")
                        .reduce<Record<string, typeof availableModels>>(
                          (acc, m) => {
                            const p = m.provider ?? "other";
                            if (!acc[p]) acc[p] = [];
                            acc[p].push(m);
                            return acc;
                          },
                          {},
                        ),
                    ).map(([provider, models]) => (
                      <div key={provider}>
                        <div className="px-3 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70">
                          {provider}
                        </div>
                        {models.map((m) => (
                          <button
                            key={m.id}
                            onClick={() => {
                              onModelChange(m.id);
                              setShowModelDropdown(false);
                            }}
                            className={cn(
                              "flex w-full items-center rounded-md px-3 py-2 text-left text-xs transition-colors",
                              model === m.id
                                ? "bg-accent text-accent-foreground"
                                : "text-foreground hover:bg-accent/50",
                            )}
                          >
                            <span className="font-medium">{m.label}</span>
                            {m.default && (
                              <span className="ml-auto text-[10px] text-muted-foreground">
                                default
                              </span>
                            )}
                          </button>
                        ))}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Right: Send / Stop */}
          <div>
            {isStreaming ? (
              <button
                onClick={onStop}
                className="flex h-7 w-7 items-center justify-center rounded-full bg-primary text-primary-foreground transition-colors"
              >
                <Square className="h-3 w-3" />
              </button>
            ) : (
              <button
                onClick={handleSend}
                disabled={!canSend}
                title={!engineReady ? "Engine not connected" : undefined}
                className={cn(
                  "flex h-7 w-7 items-center justify-center rounded-full transition-all duration-200",
                  !canSend
                    ? "bg-muted-foreground/30 text-primary-foreground opacity-30 cursor-not-allowed"
                    : "bg-primary text-primary-foreground active:scale-[0.96]",
                )}
              >
                <ArrowUp className="h-4 w-4" strokeWidth={2.5} />
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Disclaimer */}
      <p className="mt-1 text-center text-[10px] text-muted-foreground">
        AI Matrx can make mistakes. Verify important information.
      </p>
    </div>
  );
}
