import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AudioLines,
  BookOpen,
  Boxes,
  Brain,
  ChevronDown,
  ChevronRight,
  FileText,
  Image,
  Layers,
  Loader2,
  NotebookPen,
  Paperclip,
  Plus,
  RotateCcw,
  Wrench,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Slider } from "@/components/ui/slider";
import type {
  ChatAttachment,
  CloudChatRunControls,
} from "@/hooks/use-cloud-chat";
import type { CloudModelOption } from "@/lib/cloud-chat-models";
import { cn } from "@/lib/utils";

const MAX_ATTACHMENTS = 5;
const MAX_ATTACHMENT_BYTES = 200 * 1024;
const ACCEPTED_EXTENSIONS =
  ".txt,.md,.markdown,.json,.csv,.tsv,.xml,.yaml,.yml,.toml,.ini,.log,.py,.ts,.tsx,.js,.jsx,.html,.css,.sh,.sql,.rs,.go";

interface LocalToolEntry {
  name: string;
  description: string;
  category: string;
  advertised: boolean;
}

interface LocalToolsResponse {
  tools?: Array<{
    name?: string;
    description?: string;
    category?: string;
    advertised?: boolean;
  }>;
}

interface ComingSoonRow {
  label: string;
  icon: typeof Image;
}

const COMING_SOON: ComingSoonRow[] = [
  { label: "Images & media", icon: Image },
  { label: "Audio recording", icon: AudioLines },
  { label: "Notes", icon: NotebookPen },
  { label: "Documents & scratchpad", icon: FileText },
  { label: "Active context", icon: Layers },
  { label: "Sandbox binding", icon: Boxes },
  { label: "Memory", icon: Brain },
  { label: "Skills", icon: BookOpen },
];

export interface CloudChatPlusMenuProps {
  engineUrl: string | null;
  models: CloudModelOption[];
  runControls: CloudChatRunControls;
  onModelOverride: (model: string | null) => void;
  onTemperature: (temperature: number | null) => void;
  onMaxTokens: (maxTokens: number | null) => void;
  onExcludedTools: (names: string[]) => void;
  onResetOverrides: () => void;
  attachments: ChatAttachment[];
  onAddAttachments: (files: ChatAttachment[]) => void;
  disabled?: boolean;
}

function SectionHeader({
  label,
  open,
  onToggle,
  detail,
}: {
  label: string;
  open: boolean;
  onToggle: () => void;
  detail?: string;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className="flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-xs font-medium text-foreground transition-colors hover:bg-accent/50"
    >
      {open ? (
        <ChevronDown className="h-3 w-3 shrink-0 opacity-60" />
      ) : (
        <ChevronRight className="h-3 w-3 shrink-0 opacity-60" />
      )}
      <span className="flex-1 truncate">{label}</span>
      {detail && (
        <span className="max-w-[130px] truncate text-[10px] font-normal text-muted-foreground">
          {detail}
        </span>
      )}
    </button>
  );
}

export function CloudChatPlusMenu({
  engineUrl,
  models,
  runControls,
  onModelOverride,
  onTemperature,
  onMaxTokens,
  onExcludedTools,
  onResetOverrides,
  attachments,
  onAddAttachments,
  disabled = false,
}: CloudChatPlusMenuProps) {
  const [open, setOpen] = useState(false);
  const [modelSectionOpen, setModelSectionOpen] = useState(false);
  const [settingsSectionOpen, setSettingsSectionOpen] = useState(false);
  const [toolsSectionOpen, setToolsSectionOpen] = useState(false);
  const [tools, setTools] = useState<LocalToolEntry[] | null>(null);
  const [toolsLoading, setToolsLoading] = useState(false);
  const [toolsError, setToolsError] = useState<string | null>(null);
  const [attachError, setAttachError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const toolsFetchedRef = useRef(false);

  const { modelOverride, temperature, maxTokens, excludedTools } = runControls;

  // Fetch the engine tool catalog once, on first open, while the panel and
  // engine are both available.
  useEffect(() => {
    if (!open || toolsFetchedRef.current || !engineUrl) return;
    toolsFetchedRef.current = true;
    let cancelled = false;
    setToolsLoading(true);
    setToolsError(null);
    fetch(`${engineUrl}/chat/local-tools`, { signal: AbortSignal.timeout(6000) })
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return (await response.json()) as LocalToolsResponse;
      })
      .then((payload) => {
        if (cancelled) return;
        const advertised = (payload.tools ?? [])
          .filter((tool) => tool.advertised === true && Boolean(tool.name))
          .map<LocalToolEntry>((tool) => ({
            name: tool.name ?? "",
            description: tool.description ?? "",
            category: tool.category ?? "other",
            advertised: true,
          }));
        setTools(advertised);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        toolsFetchedRef.current = false;
        setToolsError(
          error instanceof Error ? error.message : "Failed to load local tools",
        );
      })
      .finally(() => {
        if (!cancelled) setToolsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, engineUrl]);

  const toolsByCategory = useMemo(() => {
    const groups = new Map<string, LocalToolEntry[]>();
    for (const tool of tools ?? []) {
      const existing = groups.get(tool.category);
      if (existing) existing.push(tool);
      else groups.set(tool.category, [tool]);
    }
    return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [tools]);

  const toggleTool = useCallback(
    (name: string) => {
      onExcludedTools(
        excludedTools.includes(name)
          ? excludedTools.filter((item) => item !== name)
          : [...excludedTools, name],
      );
    },
    [excludedTools, onExcludedTools],
  );

  const handleFiles = useCallback(
    async (fileList: FileList | null) => {
      if (!fileList || fileList.length === 0) return;
      setAttachError(null);
      const errors: string[] = [];
      const added: ChatAttachment[] = [];
      const count = attachments.length;

      for (const file of Array.from(fileList)) {
        if (count + added.length >= MAX_ATTACHMENTS) {
          errors.push(`Limit is ${MAX_ATTACHMENTS} files.`);
          break;
        }
        if (file.size > MAX_ATTACHMENT_BYTES) {
          errors.push(`${file.name} is over 200 KB.`);
          continue;
        }
        try {
          const content = await file.text();
          added.push({
            id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
            name: file.name,
            size: file.size,
            content,
          });
        } catch {
          errors.push(`Could not read ${file.name}.`);
        }
      }

      if (added.length > 0) onAddAttachments(added);
      if (errors.length > 0) setAttachError(errors.join(" "));
    },
    [attachments.length, onAddAttachments],
  );

  const overrideCount =
    (modelOverride ? 1 : 0) + (temperature != null ? 1 : 0) + (maxTokens != null ? 1 : 0);
  const hasAdjustments = overrideCount > 0 || excludedTools.length > 0;

  const modelDetail = modelOverride
    ? (models.find((m) => m.id === modelOverride)?.label ?? modelOverride)
    : "Agent default";

  const cloudModelGroups = useMemo(() => {
    return Object.entries(
      models.reduce<Record<string, CloudModelOption[]>>((acc, m) => {
        const provider = m.provider ?? "other";
        (acc[provider] ??= []).push(m);
        return acc;
      }, {}),
    );
  }, [models]);

  return (
    <Popover
      open={open && !disabled}
      onOpenChange={(next) => {
        if (!disabled) setOpen(next);
      }}
    >
      {/* asChild: PopoverTrigger renders its own <button> by default, which put a
          button inside a button — invalid HTML that breaks keyboard/AT activation
          of the inner control (MXL-D-021). asChild merges the trigger's behavior
          onto the button below instead of wrapping it. */}
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          title="Chat options"
          className={cn(
            "relative flex h-7 w-7 items-center justify-center rounded-md transition-colors",
            open || hasAdjustments
              ? "text-primary hover:text-primary"
              : "text-muted-foreground hover:text-foreground",
            disabled && "cursor-not-allowed opacity-50",
          )}
        >
          <Plus
            className={cn("h-3.5 w-3.5 transition-transform", open && "rotate-45")}
          />
          {hasAdjustments && !open && (
            <span className="absolute right-0.5 top-0.5 h-1.5 w-1.5 rounded-full bg-primary" />
          )}
        </button>
      </PopoverTrigger>

      <PopoverContent
        side="top"
        align="start"
        className="glass w-[340px] p-1.5"
      >
        <div className="max-h-[420px] overflow-y-auto">
          {/* Attach files */}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={ACCEPTED_EXTENSIONS}
            className="hidden"
            onChange={(e) => {
              void handleFiles(e.target.files);
              e.target.value = "";
            }}
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={attachments.length >= MAX_ATTACHMENTS}
            className={cn(
              "flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-xs font-medium transition-colors",
              attachments.length >= MAX_ATTACHMENTS
                ? "cursor-not-allowed text-muted-foreground/60"
                : "text-foreground hover:bg-accent/50",
            )}
          >
            <Paperclip className="h-3 w-3 shrink-0 opacity-60" />
            <span className="flex-1">Attach files</span>
            <span className="text-[10px] font-normal text-muted-foreground">
              {attachments.length > 0
                ? `${attachments.length}/${MAX_ATTACHMENTS}`
                : "Text files, 200 KB max"}
            </span>
          </button>
          {attachError && (
            <p className="px-2 pb-1 text-[10px] text-amber-500">{attachError}</p>
          )}

          <div className="my-1 border-t border-border/50" />

          {/* Model override */}
          <SectionHeader
            label="Model"
            open={modelSectionOpen}
            onToggle={() => setModelSectionOpen((prev) => !prev)}
            detail={modelDetail}
          />
          {modelSectionOpen && (
            <div className="max-h-52 overflow-y-auto pb-1 pl-4">
              <button
                type="button"
                onClick={() => onModelOverride(null)}
                className={cn(
                  "flex w-full items-center rounded-md px-2 py-1.5 text-left text-xs transition-colors",
                  !modelOverride
                    ? "bg-accent text-accent-foreground"
                    : "text-foreground hover:bg-accent/50",
                )}
              >
                Agent default
              </button>
              {models.length === 0 && (
                <p className="px-2 py-1.5 text-[11px] text-muted-foreground">
                  Cloud models are not loaded yet.
                </p>
              )}
              {cloudModelGroups.map(([provider, providerModels]) => (
                <div key={provider}>
                  <div className="px-2 pb-0.5 pt-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70">
                    {provider}
                  </div>
                  {providerModels.map((m) => (
                    <button
                      key={m.id}
                      type="button"
                      onClick={() => onModelOverride(m.id)}
                      className={cn(
                        "flex w-full items-center rounded-md px-2 py-1.5 text-left text-xs transition-colors",
                        modelOverride === m.id
                          ? "bg-accent text-accent-foreground"
                          : "text-foreground hover:bg-accent/50",
                      )}
                    >
                      <span className="truncate">{m.label}</span>
                    </button>
                  ))}
                </div>
              ))}
            </div>
          )}

          {/* Settings overrides */}
          <SectionHeader
            label="Settings"
            open={settingsSectionOpen}
            onToggle={() => setSettingsSectionOpen((prev) => !prev)}
            detail={
              overrideCount - (modelOverride ? 1 : 0) > 0 ? "Customized" : "Defaults"
            }
          />
          {settingsSectionOpen && (
            <div className="space-y-2.5 px-2 pb-2 pl-6 pt-1">
              <div>
                <div className="mb-1 flex items-center justify-between text-[11px]">
                  <span className="text-muted-foreground">Temperature</span>
                  <span className="font-medium text-foreground">
                    {temperature != null ? temperature.toFixed(1) : "Default"}
                  </span>
                </div>
                <Slider
                  min={0}
                  max={2}
                  step={0.1}
                  value={[temperature ?? 0.7]}
                  onValueChange={(value) => {
                    const next = value[0];
                    if (typeof next === "number") onTemperature(next);
                  }}
                />
              </div>
              <div>
                <div className="mb-1 text-[11px] text-muted-foreground">
                  Max output tokens
                </div>
                <Input
                  type="number"
                  min={1}
                  step={1}
                  placeholder="Default"
                  value={maxTokens ?? ""}
                  onChange={(e) => {
                    const raw = e.target.value;
                    if (!raw) {
                      onMaxTokens(null);
                      return;
                    }
                    const parsed = Number.parseInt(raw, 10);
                    onMaxTokens(Number.isFinite(parsed) && parsed > 0 ? parsed : null);
                  }}
                  className="h-7 text-xs"
                />
              </div>
              <button
                type="button"
                onClick={onResetOverrides}
                disabled={overrideCount === 0}
                className={cn(
                  "flex items-center gap-1 text-[11px] transition-colors",
                  overrideCount === 0
                    ? "cursor-not-allowed text-muted-foreground/50"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                <RotateCcw className="h-3 w-3" />
                Reset model and settings
              </button>
            </div>
          )}

          {/* Local tools */}
          <SectionHeader
            label="Tools on this computer"
            open={toolsSectionOpen}
            onToggle={() => setToolsSectionOpen((prev) => !prev)}
            detail={
              !engineUrl
                ? "Engine offline"
                : excludedTools.length > 0
                  ? `${excludedTools.length} disabled`
                  : "All enabled"
            }
          />
          {toolsSectionOpen && (
            <div className="max-h-56 overflow-y-auto pb-1 pl-4">
              {!engineUrl && (
                <p className="px-2 py-1.5 text-[11px] text-muted-foreground">
                  Connect the local engine to manage tools.
                </p>
              )}
              {toolsLoading && (
                <p className="flex items-center gap-1.5 px-2 py-1.5 text-[11px] text-muted-foreground">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Loading tools...
                </p>
              )}
              {toolsError && (
                <p className="px-2 py-1.5 text-[11px] text-amber-500">{toolsError}</p>
              )}
              {tools && tools.length === 0 && !toolsLoading && (
                <p className="px-2 py-1.5 text-[11px] text-muted-foreground">
                  No advertised tools found.
                </p>
              )}
              {toolsByCategory.map(([category, categoryTools]) => (
                <div key={category}>
                  <div className="flex items-center gap-1 px-2 pb-0.5 pt-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70">
                    <Wrench className="h-2.5 w-2.5" />
                    {category}
                  </div>
                  {categoryTools.map((tool) => (
                    <label
                      key={tool.name}
                      title={tool.description}
                      className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-xs text-foreground transition-colors hover:bg-accent/50"
                    >
                      <Checkbox
                        checked={!excludedTools.includes(tool.name)}
                        onCheckedChange={() => toggleTool(tool.name)}
                        className="h-3 w-3"
                      />
                      <span className="truncate">{tool.name}</span>
                    </label>
                  ))}
                </div>
              ))}
            </div>
          )}

          <div className="my-1 border-t border-border/50" />

          {/* Coming soon */}
          {COMING_SOON.map(({ label, icon: Icon }) => (
            <div
              key={label}
              aria-disabled="true"
              className="flex cursor-not-allowed items-center gap-1.5 rounded-md px-2 py-1.5 text-xs text-muted-foreground/60"
            >
              <Icon className="h-3 w-3 shrink-0 opacity-50" />
              <span className="flex-1">{label}</span>
              <Badge
                variant="outline"
                className="h-4 px-1.5 text-[9px] font-medium text-muted-foreground/70"
              >
                Soon
              </Badge>
            </div>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}
