/**
 * Configurations — centralized settings page.
 *
 * A single scrollable page with multi-column layout displaying every
 * user-configurable option in the app. Each section has its own
 * save/cancel buttons that appear only when that section has unsaved changes.
 * A global floating save bar appears when any section is dirty.
 *
 * All enum-like fields use real data: model catalogs from Rust/Tauri,
 * audio devices from CPAL, system prompts from the prompt library, and
 * cloud AI models from the engine database.
 */

import { useCallback, useState, useEffect, useRef } from "react";
import {
  Settings2,
  Palette,
  MessageSquare,
  BrainCircuit,
  Mic,
  Radio,
  Globe,
  Network,
  Bell,
  Save,
  X,
  RotateCcw,
  Loader2,
  RefreshCw,
  Star,
  Cpu,
  CheckCircle2,
  AlertCircle,
  CloudOff,
  Cloud,
  Volume2,
} from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Slider } from "@/components/ui/slider";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useConfigurations,
  type ConfigSection,
} from "@/hooks/use-configurations";
import { useConfigCatalogs } from "@/hooks/use-config-catalogs";
import type { AppSettings, SyncResult } from "@/lib/settings";
import { cn } from "@/lib/utils";

// ── Section save/cancel bar ──────────────────────────────────────────────────

/**
 * Shows Save/Cancel when dirty.
 * After saving, shows a per-step status row (local ✓, engine ✓/✗, cloud ✓/✗)
 * that auto-clears after 6 seconds.
 */
function SectionActions({
  section,
  dirty,
  saving: savingProp,
  saveError: saveErrorProp,
  lastSyncResult: lastSyncResultProp,
  activeSection,
  onSave,
  onCancel,
}: {
  section: ConfigSection;
  dirty: boolean;
  saving: boolean;
  saveError: string | null;
  lastSyncResult: SyncResult | null;
  /** Which section's save is in flight / just completed (page-level). */
  activeSection: ConfigSection | null;
  onSave: (s: ConfigSection) => void;
  onCancel: (s: ConfigSection) => void;
}) {
  // The saving flag and sync result are shared across all sections by
  // use-configurations — only honor them for the section whose save is
  // actually in flight / just completed, otherwise every dirty section
  // shows "Saving…" and every section shows the post-save status row.
  const isMine = activeSection === section;
  const saving = savingProp && isMine;
  const saveError = isMine ? saveErrorProp : null;
  const lastSyncResult = isMine ? lastSyncResultProp : null;

  const [recentResult, setRecentResult] = useState<SyncResult | null>(null);
  const [recentError, setRecentError] = useState<string | null>(null);
  const clearTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // When a save completes (saving goes false and we have a result), capture it
  // and auto-clear after 6 seconds.
  useEffect(() => {
    if (!saving && lastSyncResult) {
      setRecentResult(lastSyncResult);
      setRecentError(saveError);
      if (clearTimer.current) clearTimeout(clearTimer.current);
      clearTimer.current = setTimeout(() => {
        setRecentResult(null);
        setRecentError(null);
      }, 6000);
    }
    return () => {
      if (clearTimer.current) clearTimeout(clearTimer.current);
    };
  }, [saving, lastSyncResult, saveError]);

  // Clear status when the section becomes dirty again (user made new changes)
  useEffect(() => {
    if (dirty) {
      setRecentResult(null);
      setRecentError(null);
    }
  }, [dirty]);

  const showStatus = !dirty && !saving && recentResult !== null;

  if (!dirty && !saving && !showStatus) return null;

  return (
    <div className="pt-3 border-t mt-3 space-y-2">
      {(dirty || saving) && (
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            onClick={() => onSave(section)}
            disabled={saving}
            className="gap-1.5"
          >
            {saving ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Save className="h-3.5 w-3.5" />
            )}
            {saving ? "Saving…" : "Save"}
          </Button>
          {!saving && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => onCancel(section)}
              className="gap-1.5"
            >
              <RotateCcw className="h-3.5 w-3.5" />
              Cancel
            </Button>
          )}
        </div>
      )}

      {showStatus && (
        <SyncStatusRow result={recentResult!} error={recentError} />
      )}
    </div>
  );
}

function SyncStatusRow({
  result,
  error,
}: {
  result: SyncResult;
  error: string | null;
}) {
  const engineOk = result.engine === "ok";
  const engineSkipped = result.engine === "skipped";
  const cloudOk = result.cloud === "ok";
  const cloudSkipped = result.cloud === "skipped";

  return (
    <div className="flex flex-wrap items-center gap-3 text-xs">
      {/* Local always ok */}
      <span className="flex items-center gap-1 text-green-500">
        <CheckCircle2 className="h-3 w-3" />
        Saved locally
      </span>

      {/* Engine */}
      {engineOk && (
        <span className="flex items-center gap-1 text-green-500">
          <CheckCircle2 className="h-3 w-3" />
          Engine synced
        </span>
      )}
      {engineSkipped && (
        <span className="flex items-center gap-1 text-muted-foreground">
          <CloudOff className="h-3 w-3" />
          Engine offline
        </span>
      )}
      {!engineOk && !engineSkipped && (
        <span
          className="flex items-center gap-1 text-destructive"
          title={result.engine}
        >
          <AlertCircle className="h-3 w-3" />
          Engine error
        </span>
      )}

      {/* Cloud */}
      {engineOk && cloudOk && (
        <span className="flex items-center gap-1 text-green-500">
          <Cloud className="h-3 w-3" />
          Cloud synced
        </span>
      )}
      {engineOk && cloudSkipped && (
        <span className="flex items-center gap-1 text-muted-foreground">
          <CloudOff className="h-3 w-3" />
          Not signed in
        </span>
      )}
      {engineOk && !cloudOk && !cloudSkipped && (
        <span
          className="flex items-center gap-1 text-amber-500"
          title={result.cloud}
        >
          <AlertCircle className="h-3 w-3" />
          Cloud sync failed
        </span>
      )}

      {error && <p className="w-full text-destructive">{error}</p>}
    </div>
  );
}

// ── Reusable field components ────────────────────────────────────────────────

function SettingRow({
  label,
  description,
  children,
}: {
  label: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-4 py-2">
      <div className="flex-1 min-w-0">
        <Label className="text-sm font-medium">{label}</Label>
        {description && (
          <p className="text-xs text-muted-foreground mt-0.5">{description}</p>
        )}
      </div>
      <div className="flex-shrink-0">{children}</div>
    </div>
  );
}

function NumberInput({
  value,
  onChange,
  min,
  max,
  step,
  className,
}: {
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  className?: string;
}) {
  return (
    <Input
      type="number"
      value={value}
      onChange={(e) => onChange(Number(e.target.value))}
      min={min}
      max={max}
      step={step}
      className={cn("w-24 text-right", className)}
    />
  );
}

function SliderRow({
  label,
  description,
  value,
  onChange,
  min,
  max,
  step,
  decimals = 2,
}: {
  label: string;
  description?: string;
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  step: number;
  decimals?: number;
}) {
  return (
    <SettingRow label={label} {...(description !== undefined ? { description } : {})}>
      <div className="flex items-center gap-2">
        <Slider
          value={[value]}
          onValueChange={([v]) => onChange(v)}
          min={min}
          max={max}
          step={step}
          className="w-24"
        />
        <span className="text-xs text-muted-foreground w-8 text-right tabular-nums">
          {value.toFixed(decimals)}
        </span>
      </div>
    </SettingRow>
  );
}

/** Format bytes to human-readable size */
function fmtSize(gb: number): string {
  if (gb < 1) return `${Math.round(gb * 1024)} MB`;
  return `${gb.toFixed(1)} GB`;
}

// ── Compact fields for horizontal layouts ───────────────────────────────────

function CompactSlider({
  label,
  value,
  onChange,
  min,
  max,
  step,
  decimals = 2,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  step: number;
  decimals?: number;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <Label className="text-xs font-medium">{label}</Label>
        <span className="text-xs text-muted-foreground tabular-nums">
          {value.toFixed(decimals)}
        </span>
      </div>
      <Slider
        value={[value]}
        onValueChange={([v]) => onChange(v)}
        min={min}
        max={max}
        step={step}
      />
    </div>
  );
}

function CompactNumber({
  label,
  value,
  onChange,
  min,
  max,
  step,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
}) {
  return (
    <div className="space-y-1.5">
      <Label className="text-xs font-medium">{label}</Label>
      <Input
        type="number"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        min={min}
        max={max}
        step={step}
        className="h-9 text-right"
      />
    </div>
  );
}

function CompactSwitch({
  label,
  checked,
  onCheckedChange,
}: {
  label: string;
  checked: boolean;
  onCheckedChange: (v: boolean) => void;
}) {
  return (
    <div className="space-y-1.5">
      <Label className="text-xs font-medium">{label}</Label>
      <div className="h-9 flex items-center">
        <Switch checked={checked} onCheckedChange={onCheckedChange} />
      </div>
    </div>
  );
}

function CompactSelect({
  label,
  value,
  onValueChange,
  placeholder,
  loading,
  children,
}: {
  label: string;
  value: string;
  onValueChange: (v: string) => void;
  placeholder?: string;
  loading?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label className="text-xs font-medium">{label}</Label>
      <div className="flex items-center gap-1.5">
        <Select value={value} onValueChange={onValueChange}>
          <SelectTrigger className="h-9 w-full">
            <SelectValue placeholder={placeholder} />
          </SelectTrigger>
          <SelectContent>{children}</SelectContent>
        </Select>
        {loading && (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground flex-shrink-0" />
        )}
      </div>
    </div>
  );
}

// ── Update check interval dropdown ──────────────────────────────────────────

const UPDATE_INTERVAL_PRESETS: { label: string; value: number }[] = [
  { label: "Every 5 minutes", value: 5 },
  { label: "Every 15 minutes", value: 15 },
  { label: "Every 30 minutes", value: 30 },
  { label: "Hourly", value: 60 },
  { label: "Every 2 hours", value: 120 },
  { label: "Every 4 hours", value: 240 },
  { label: "Every 8 hours", value: 480 },
  { label: "Once a day", value: 1440 },
];

const PRESET_VALUES = new Set<number>(
  UPDATE_INTERVAL_PRESETS.map((p) => p.value),
);

function UpdateIntervalRow({
  value,
  onChange,
}: {
  value: number;
  onChange: (v: number) => void;
}) {
  const isCustom = !PRESET_VALUES.has(value);
  // "Custom..." doesn't change `value`, so track the user's choice explicitly
  // — otherwise selectValue snaps back to the preset and the input never shows.
  const [customMode, setCustomMode] = useState(isCustom);
  const [customInput, setCustomInput] = useState(isCustom ? String(value) : "");

  const handleSelect = (v: string) => {
    if (v === "__custom__") {
      setCustomMode(true);
      setCustomInput(String(value));
      return;
    }
    setCustomMode(false);
    onChange(Number(v));
  };

  const selectValue = isCustom || customMode ? "__custom__" : String(value);

  return (
    <SettingRow
      label="Update check frequency"
      description="How often the app checks for new updates"
    >
      <div className="flex items-center gap-2">
        <Select value={selectValue} onValueChange={handleSelect}>
          <SelectTrigger className="w-44">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {UPDATE_INTERVAL_PRESETS.map((p) => (
              <SelectItem key={p.value} value={String(p.value)}>
                {p.label}
              </SelectItem>
            ))}
            <Separator className="my-1" />
            <SelectItem value="__custom__">Custom...</SelectItem>
          </SelectContent>
        </Select>
        {(isCustom || selectValue === "__custom__") && (
          <div className="flex items-center gap-1">
            <Input
              type="number"
              value={customInput}
              onChange={(e) => setCustomInput(e.target.value)}
              onBlur={() => {
                const v = Math.max(5, Number(customInput) || 60);
                setCustomInput(String(v));
                onChange(v);
              }}
              min={5}
              className="w-20 text-right"
            />
            <span className="text-xs text-muted-foreground">min</span>
          </div>
        )}
      </div>
    </SettingRow>
  );
}

// ── GPU layers dropdown ──────────────────────────────────────────────────────

const GPU_LAYERS_PRESETS: { label: string; value: number }[] = [
  { label: "Auto (recommended)", value: -1 },
  { label: "None (CPU only)", value: 0 },
  { label: "8 layers", value: 8 },
  { label: "16 layers", value: 16 },
  { label: "24 layers", value: 24 },
  { label: "32 layers", value: 32 },
  { label: "All layers", value: 999 },
];

const GPU_PRESET_VALUES = new Set<number>(
  GPU_LAYERS_PRESETS.map((p) => p.value),
);

function GpuLayersRow({
  value,
  onChange,
}: {
  value: number;
  onChange: (v: number) => void;
}) {
  const isCustom = !GPU_PRESET_VALUES.has(value);
  const [customMode, setCustomMode] = useState(isCustom);
  const [customInput, setCustomInput] = useState(isCustom ? String(value) : "");

  const handleSelect = (v: string) => {
    if (v === "__custom__") {
      setCustomMode(true);
      setCustomInput(String(value));
      return;
    }
    setCustomMode(false);
    onChange(Number(v));
  };

  const selectValue = isCustom || customMode ? "__custom__" : String(value);

  return (
    <div className="flex items-center gap-2">
      <Select value={selectValue} onValueChange={handleSelect}>
        <SelectTrigger className="w-44">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {GPU_LAYERS_PRESETS.map((p) => (
            <SelectItem key={p.value} value={String(p.value)}>
              {p.label}
            </SelectItem>
          ))}
          <Separator className="my-1" />
          <SelectItem value="__custom__">Custom...</SelectItem>
        </SelectContent>
      </Select>
      {(isCustom || selectValue === "__custom__") && (
        <div className="flex items-center gap-1">
          <Input
            type="number"
            value={customInput}
            onChange={(e) => setCustomInput(e.target.value)}
            onBlur={() => {
              const v = Math.max(0, Number(customInput) || 0);
              setCustomInput(String(v));
              onChange(v);
            }}
            min={0}
            max={999}
            className="w-20 text-right"
          />
          <span className="text-xs text-muted-foreground">layers</span>
        </div>
      )}
    </div>
  );
}

// ── Processing timeout dropdown ──────────────────────────────────────────────

const TIMEOUT_PRESETS: { label: string; value: number }[] = [
  { label: "5 seconds", value: 5000 },
  { label: "10 seconds", value: 10000 },
  { label: "15 seconds", value: 15000 },
  { label: "30 seconds", value: 30000 },
  { label: "1 minute", value: 60000 },
  { label: "2 minutes", value: 120000 },
  { label: "5 minutes", value: 300000 },
];

const TIMEOUT_PRESET_VALUES = new Set<number>(
  TIMEOUT_PRESETS.map((p) => p.value),
);

function ProcessingTimeoutRow({
  value,
  onChange,
}: {
  value: number;
  onChange: (v: number) => void;
}) {
  const isCustom = !TIMEOUT_PRESET_VALUES.has(value);
  const [customMode, setCustomMode] = useState(isCustom);
  const [customInput, setCustomInput] = useState(
    isCustom ? String(Math.round(value / 1000)) : "",
  );

  const handleSelect = (v: string) => {
    if (v === "__custom__") {
      setCustomMode(true);
      setCustomInput(String(Math.round(value / 1000)));
      return;
    }
    setCustomMode(false);
    onChange(Number(v));
  };

  const selectValue = isCustom || customMode ? "__custom__" : String(value);

  return (
    <SettingRow
      label="Processing timeout"
      description="Force-reset if transcription gets stuck"
    >
      <div className="flex items-center gap-2">
        <Select value={selectValue} onValueChange={handleSelect}>
          <SelectTrigger className="w-36">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {TIMEOUT_PRESETS.map((p) => (
              <SelectItem key={p.value} value={String(p.value)}>
                {p.label}
              </SelectItem>
            ))}
            <Separator className="my-1" />
            <SelectItem value="__custom__">Custom...</SelectItem>
          </SelectContent>
        </Select>
        {(isCustom || selectValue === "__custom__") && (
          <div className="flex items-center gap-1">
            <Input
              type="number"
              value={customInput}
              onChange={(e) => setCustomInput(e.target.value)}
              onBlur={() => {
                const secs = Math.max(1, Number(customInput) || 15);
                setCustomInput(String(secs));
                onChange(secs * 1000);
              }}
              min={1}
              className="w-16 text-right"
            />
            <span className="text-xs text-muted-foreground">sec</span>
          </div>
        )}
      </div>
    </SettingRow>
  );
}

// ── Scrape delay dropdown ────────────────────────────────────────────────────

const SCRAPE_DELAY_PRESETS: { label: string; value: string }[] = [
  { label: "No delay", value: "0" },
  { label: "Half a second", value: "0.5" },
  { label: "1 second", value: "1.0" },
  { label: "2 seconds", value: "2.0" },
  { label: "3 seconds", value: "3.0" },
  { label: "5 seconds", value: "5.0" },
  { label: "10 seconds", value: "10.0" },
  { label: "30 seconds", value: "30.0" },
];

const SCRAPE_PRESET_VALUES = new Set<string>(
  SCRAPE_DELAY_PRESETS.map((p) => p.value),
);

function ScrapeDelayRow({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const isCustom = !SCRAPE_PRESET_VALUES.has(value);
  const [customMode, setCustomMode] = useState(isCustom);
  const [customInput, setCustomInput] = useState(isCustom ? value : "");

  const handleSelect = (v: string) => {
    if (v === "__custom__") {
      setCustomMode(true);
      setCustomInput(value);
      return;
    }
    setCustomMode(false);
    onChange(v);
  };

  const selectValue = isCustom || customMode ? "__custom__" : value;

  return (
    <SettingRow
      label="Delay between requests"
      description="Pause between page loads to avoid getting blocked"
    >
      <div className="flex items-center gap-2">
        <Select value={selectValue} onValueChange={handleSelect}>
          <SelectTrigger className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {SCRAPE_DELAY_PRESETS.map((p) => (
              <SelectItem key={p.value} value={p.value}>
                {p.label}
              </SelectItem>
            ))}
            <Separator className="my-1" />
            <SelectItem value="__custom__">Custom...</SelectItem>
          </SelectContent>
        </Select>
        {(isCustom || selectValue === "__custom__") && (
          <div className="flex items-center gap-1">
            <Input
              type="number"
              value={customInput}
              onChange={(e) => setCustomInput(e.target.value)}
              onBlur={() => {
                const v = Math.max(0, Number(customInput) || 1);
                const s = v.toFixed(1);
                setCustomInput(s);
                onChange(s);
              }}
              min={0}
              step={0.5}
              className="w-16 text-right"
            />
            <span className="text-xs text-muted-foreground">sec</span>
          </div>
        )}
      </div>
    </SettingRow>
  );
}

// ── Main Page ────────────────────────────────────────────────────────────────

export function Configurations() {
  const [state, actions] = useConfigurations();
  const {
    draft,
    sectionDirty,
    isGlobalDirty,
    isSaving,
    saveError,
    lastSyncResult,
  } = state;
  const catalogs = useConfigCatalogs();

  const set = actions.set;
  // Track which section's save is in flight / just completed so the shared
  // isSaving/lastSyncResult from use-configurations only surface under the
  // section that actually triggered the save.
  const [activeSaveSection, setActiveSaveSection] =
    useState<ConfigSection | null>(null);
  const handleSave = useCallback(
    (s: ConfigSection) => {
      setActiveSaveSection(s);
      actions.saveSection(s);
    },
    [actions],
  );
  const handleCancel = useCallback(
    (s: ConfigSection) => actions.cancelSection(s),
    [actions],
  );

  // Convenience: common props for every SectionActions instance
  const sectionActionProps = {
    saving: isSaving,
    saveError,
    lastSyncResult: lastSyncResult ?? null,
    activeSection: activeSaveSection,
    onSave: handleSave,
    onCancel: handleCancel,
  };

  if (!draft) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PageHeader
        title="Configurations"
        description="Centralized control for every setting in the application"
      />

      <ScrollArea className="flex-1">
        <div className="p-6 space-y-6">
          {/* 3-column responsive grid */}
          <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-6">
            {/* ── Application ──────────────────────────────────── */}
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Settings2 className="h-4 w-4" />
                  Application
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-1">
                <SettingRow
                  label="Computer Name"
                  description="Identify this computer in cloud sync"
                >
                  <Input
                    value={draft.instanceName}
                    onChange={(e) => set("instanceName", e.target.value)}
                    className="w-40"
                  />
                </SettingRow>
                <Separator className="my-2" />
                <SettingRow
                  label="Launch on startup"
                  description="Start the app when your computer boots"
                >
                  <Switch
                    checked={draft.launchOnStartup}
                    onCheckedChange={(v) => set("launchOnStartup", v)}
                  />
                </SettingRow>
                <SettingRow
                  label="Minimize to tray"
                  description="Keep running in system tray when closed"
                >
                  <Switch
                    checked={draft.minimizeToTray}
                    onCheckedChange={(v) => set("minimizeToTray", v)}
                  />
                </SettingRow>
                <Separator className="my-2" />
                <SettingRow label="Auto-check for updates">
                  <Switch
                    checked={draft.autoCheckUpdates}
                    onCheckedChange={(v) => set("autoCheckUpdates", v)}
                  />
                </SettingRow>
                <UpdateIntervalRow
                  value={draft.updateCheckInterval}
                  onChange={(v) => set("updateCheckInterval", v)}
                />
                <SectionActions
                  section="application"
                  dirty={sectionDirty.application}
                  {...sectionActionProps}
                />
              </CardContent>
            </Card>

            {/* ── Appearance ──────────────────────────────────── */}
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Palette className="h-4 w-4" />
                  Appearance
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-1">
                <SettingRow label="Theme" description="App color scheme">
                  <Select
                    value={draft.theme}
                    onValueChange={(v) =>
                      set("theme", v as AppSettings["theme"])
                    }
                  >
                    <SelectTrigger className="w-32">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="dark">Dark</SelectItem>
                      <SelectItem value="light">Light</SelectItem>
                      <SelectItem value="system">System</SelectItem>
                    </SelectContent>
                  </Select>
                </SettingRow>
                <SettingRow
                  label="Sidebar collapsed by default"
                  description="Start with sidebar minimized"
                >
                  <Switch
                    checked={draft.sidebarCollapsed}
                    onCheckedChange={(v) => set("sidebarCollapsed", v)}
                  />
                </SettingRow>
                <SectionActions
                  section="appearance"
                  dirty={sectionDirty.appearance}
                  {...sectionActionProps}
                />
              </CardContent>
            </Card>

            {/* ── Chat & AI ──────────────────────────────────── */}
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="flex items-center gap-2 text-base">
                  <MessageSquare className="h-4 w-4" />
                  Chat & AI
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-1">
                <SettingRow
                  label="Default AI model"
                  description="Cloud model for new conversations"
                >
                  <div className="flex items-center gap-1.5">
                    <Select
                      value={draft.chatDefaultModel}
                      onValueChange={(v) => set("chatDefaultModel", v)}
                    >
                      <SelectTrigger className="w-52">
                        <SelectValue placeholder="Select model..." />
                      </SelectTrigger>
                      <SelectContent>
                        {catalogs.chatModels.length === 0 ? (
                          <div className="py-3 px-2 text-center text-xs text-muted-foreground">
                            {catalogs.chatModelsLoading
                              ? "Loading models…"
                              : "No models available — connect to engine"}
                          </div>
                        ) : (
                          (() => {
                            const groups: Record<
                              string,
                              typeof catalogs.chatModels
                            > = {};
                            for (const m of catalogs.chatModels) {
                              const p = m.provider;
                              if (!groups[p]) groups[p] = [];
                              groups[p].push(m);
                            }
                            return Object.entries(groups).map(
                              ([provider, models]) => (
                                <SelectGroup key={provider}>
                                  <SelectLabel className="capitalize">
                                    {provider}
                                  </SelectLabel>
                                  {models.map((m) => (
                                    <SelectItem key={m.id} value={m.id}>
                                      <span className="flex items-center gap-1.5">
                                        {m.label}
                                        {m.is_primary && (
                                          <Star className="h-3 w-3 text-yellow-500" />
                                        )}
                                      </span>
                                    </SelectItem>
                                  ))}
                                </SelectGroup>
                              ),
                            );
                          })()
                        )}
                      </SelectContent>
                    </Select>
                    {catalogs.chatModelsLoading && (
                      <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                    )}
                  </div>
                </SettingRow>
                <SettingRow label="Default chat mode">
                  <Select
                    value={draft.chatDefaultMode}
                    onValueChange={(v) =>
                      set(
                        "chatDefaultMode",
                        v as AppSettings["chatDefaultMode"],
                      )
                    }
                  >
                    <SelectTrigger className="w-32">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="chat">Chat</SelectItem>
                      <SelectItem value="co-work">Co-work</SelectItem>
                      <SelectItem value="code">Code</SelectItem>
                    </SelectContent>
                  </Select>
                </SettingRow>
                <SettingRow
                  label="Max conversations"
                  description="Maximum stored chat conversations"
                >
                  <NumberInput
                    value={draft.chatMaxConversations}
                    onChange={(v) =>
                      set("chatMaxConversations", Math.max(1, v))
                    }
                    min={1}
                    max={500}
                  />
                </SettingRow>
                <SettingRow
                  label="System prompt"
                  description="Personality for new conversations"
                >
                  <Select
                    value={draft.chatDefaultSystemPromptId || "__builtin__"}
                    onValueChange={(v) =>
                      set(
                        "chatDefaultSystemPromptId",
                        v === "__builtin__" ? "" : v,
                      )
                    }
                  >
                    <SelectTrigger className="w-52">
                      <SelectValue placeholder="Built-in Assistant" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__builtin__">
                        <span className="text-muted-foreground">
                          Default (Built-in Assistant)
                        </span>
                      </SelectItem>
                      {catalogs.systemPromptOptions.length > 0 && (
                        <Separator className="my-1" />
                      )}
                      {(() => {
                        const groups: Record<
                          string,
                          typeof catalogs.systemPromptOptions
                        > = {};
                        for (const p of catalogs.systemPromptOptions) {
                          if (!groups[p.category]) groups[p.category] = [];
                          groups[p.category].push(p);
                        }
                        return Object.entries(groups).map(([cat, prompts]) => (
                          <SelectGroup key={cat}>
                            <SelectLabel>{cat}</SelectLabel>
                            {prompts.map((p) => (
                              <SelectItem key={p.id} value={p.id}>
                                {p.name}
                              </SelectItem>
                            ))}
                          </SelectGroup>
                        ));
                      })()}
                    </SelectContent>
                  </Select>
                </SettingRow>
                <SectionActions
                  section="chatAi"
                  dirty={sectionDirty.chatAi}
                  {...sectionActionProps}
                />
              </CardContent>
            </Card>

            {/* ── Local LLM ──────────────────────────────────── */}
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="flex items-center gap-2 text-base">
                  <BrainCircuit className="h-4 w-4" />
                  Local LLM
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-1">
                <SettingRow
                  label="Default model"
                  description="Downloaded model used when starting the LLM server"
                >
                  <div className="flex items-center gap-1.5">
                    <Select
                      value={draft.llmDefaultModel || "__auto__"}
                      onValueChange={(v) =>
                        set("llmDefaultModel", v === "__auto__" ? "" : v)
                      }
                    >
                      <SelectTrigger className="w-56">
                        <SelectValue placeholder="Auto-detect" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="__auto__">
                          <span className="flex items-center gap-1.5">
                            <Cpu className="h-3 w-3" />
                            Auto (recommended for your hardware)
                          </span>
                        </SelectItem>
                        {catalogs.llmModels.length > 0 && (
                          <Separator className="my-1" />
                        )}
                        {catalogs.llmModels.map((m) => (
                          <SelectItem key={m.filename} value={m.filename}>
                            <div className="flex flex-col gap-0.5">
                              <span className="flex items-center gap-1.5">
                                {m.name}
                                {m.filename === catalogs.llmRecommended && (
                                  <span className="text-[10px] font-medium bg-primary/15 text-primary px-1.5 py-0.5 rounded">
                                    Recommended
                                  </span>
                                )}
                              </span>
                              <span className="text-[10px] text-muted-foreground">
                                {fmtSize(m.disk_size_gb)} disk
                                {" / "}
                                {fmtSize(m.ram_required_gb)} RAM
                                {" / "}
                                {m.speed}
                                {" / "}
                                Tool calling:{" "}
                                {"★".repeat(m.tool_calling_rating)}
                                {"☆".repeat(5 - m.tool_calling_rating)}
                              </span>
                            </div>
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    {catalogs.llmModelsLoading && (
                      <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                    )}
                  </div>
                </SettingRow>
                <SettingRow
                  label="GPU layers"
                  description="How many layers to offload to your GPU"
                >
                  <GpuLayersRow
                    value={draft.llmDefaultGpuLayers}
                    onChange={(v) => set("llmDefaultGpuLayers", v)}
                  />
                </SettingRow>
                <SettingRow
                  label="Context length"
                  description="Default max tokens when starting a model (override per custom model on the Models tab)"
                >
                  <Select
                    value={String(draft.llmDefaultContextLength)}
                    onValueChange={(v) =>
                      set("llmDefaultContextLength", Number(v))
                    }
                  >
                    <SelectTrigger className="w-32">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="2048">2,048</SelectItem>
                      <SelectItem value="4096">4,096</SelectItem>
                      <SelectItem value="8192">8,192</SelectItem>
                      <SelectItem value="16384">16,384</SelectItem>
                      <SelectItem value="32768">32,768</SelectItem>
                      <SelectItem value="65536">65,536</SelectItem>
                      <SelectItem value="131072">131,072</SelectItem>
                      <SelectItem value="262144">262,144</SelectItem>
                    </SelectContent>
                  </Select>
                </SettingRow>
                <SettingRow
                  label="Auto-start server"
                  description="Launch LLM server when app starts"
                >
                  <Switch
                    checked={draft.llmAutoStartServer}
                    onCheckedChange={(v) => set("llmAutoStartServer", v)}
                  />
                </SettingRow>
                <SectionActions
                  section="localLlm"
                  dirty={sectionDirty.localLlm}
                  {...sectionActionProps}
                />
              </CardContent>
            </Card>

            {/* ── Voice & Transcription ───────────────────────── */}
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Mic className="h-4 w-4" />
                  Voice & Transcription
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-1">
                <SettingRow
                  label="Default Whisper model"
                  description="Speech-to-text model quality"
                >
                  <div className="flex items-center gap-1.5">
                    <Select
                      value={draft.transcriptionDefaultModel || "__auto__"}
                      onValueChange={(v) =>
                        set(
                          "transcriptionDefaultModel",
                          v === "__auto__" ? "" : v,
                        )
                      }
                    >
                      <SelectTrigger className="w-52">
                        <SelectValue placeholder="Auto-detect" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="__auto__">
                          <span className="flex items-center gap-1.5">
                            <Cpu className="h-3 w-3" />
                            Auto-detect (hardware-based)
                          </span>
                        </SelectItem>
                        {catalogs.whisperModels.length > 0 && (
                          <Separator className="my-1" />
                        )}
                        {catalogs.whisperModels.map((m) => (
                          <SelectItem key={m.filename} value={m.filename}>
                            <div className="flex flex-col gap-0.5">
                              <span className="flex items-center gap-1.5">
                                {m.filename
                                  .replace("ggml-", "")
                                  .replace(".bin", "")}
                                {m.filename === catalogs.whisperRecommended && (
                                  <span className="text-[10px] font-medium bg-primary/15 text-primary px-1.5 py-0.5 rounded">
                                    Recommended
                                  </span>
                                )}
                              </span>
                              <span className="text-[10px] text-muted-foreground">
                                {m.download_size_mb} MB download
                                {" / "}
                                {m.ram_required_mb} MB RAM
                                {" / "}
                                {m.relative_speed}
                                {" / "}
                                {m.accuracy}
                              </span>
                            </div>
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    {catalogs.whisperModelsLoading && (
                      <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                    )}
                  </div>
                </SettingRow>
                <SettingRow
                  label="Auto-initialize on startup"
                  description="Load transcription model when app starts"
                >
                  <Switch
                    checked={draft.transcriptionAutoInit}
                    onCheckedChange={(v) => set("transcriptionAutoInit", v)}
                  />
                </SettingRow>
                <SettingRow
                  label="Audio input device"
                  description="Microphone for voice capture"
                >
                  <div className="flex items-center gap-1.5">
                    <Select
                      value={draft.transcriptionAudioDevice || "__default__"}
                      onValueChange={(v) =>
                        set(
                          "transcriptionAudioDevice",
                          v === "__default__" ? "" : v,
                        )
                      }
                    >
                      <SelectTrigger className="w-52">
                        <SelectValue placeholder="System default" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="__default__">
                          <span className="text-muted-foreground">
                            System default
                          </span>
                        </SelectItem>
                        {catalogs.audioDevices.length > 0 && (
                          <Separator className="my-1" />
                        )}
                        {catalogs.audioDevices.map((d) => (
                          <SelectItem key={d.name} value={d.name}>
                            <span className="flex items-center gap-1.5">
                              {d.name}
                              {d.is_default && (
                                <span className="text-[10px] font-medium bg-green-500/15 text-green-500 px-1.5 py-0.5 rounded">
                                  Default
                                </span>
                              )}
                            </span>
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-8 w-8"
                      onClick={() => catalogs.refreshAudioDevices()}
                      disabled={catalogs.audioDevicesLoading}
                      title="Refresh devices"
                    >
                      {catalogs.audioDevicesLoading ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <RefreshCw className="h-3.5 w-3.5" />
                      )}
                    </Button>
                  </div>
                </SettingRow>
                <ProcessingTimeoutRow
                  value={draft.transcriptionProcessingTimeout}
                  onChange={(v) => set("transcriptionProcessingTimeout", v)}
                />
                <SectionActions
                  section="voice"
                  dirty={sectionDirty.voice}
                  {...sectionActionProps}
                />
              </CardContent>
            </Card>

            {/* ── Wake Word ──────────────────────────────────── */}
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Radio className="h-4 w-4" />
                  Wake Word
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-1">
                <SettingRow
                  label="Enabled"
                  description="Master switch for wake word detection"
                >
                  <Switch
                    checked={draft.wakeWordEnabled}
                    onCheckedChange={(v) => set("wakeWordEnabled", v)}
                  />
                </SettingRow>
                <SettingRow
                  label="Listen on startup"
                  description="Start listening when app launches"
                >
                  <Switch
                    checked={draft.wakeWordListenOnStartup}
                    onCheckedChange={(v) => set("wakeWordListenOnStartup", v)}
                  />
                </SettingRow>
                <SettingRow
                  label="Detection engine"
                  description="Whisper = flexible keyword, OWW = trained models"
                >
                  <Select
                    value={draft.wakeWordEngine}
                    onValueChange={(v) =>
                      set("wakeWordEngine", v as AppSettings["wakeWordEngine"])
                    }
                  >
                    <SelectTrigger className="w-44">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="whisper">
                        <div className="flex flex-col">
                          <span>Whisper</span>
                          <span className="text-[10px] text-muted-foreground">
                            Custom keyword phrase
                          </span>
                        </div>
                      </SelectItem>
                      <SelectItem value="oww">
                        <div className="flex flex-col">
                          <span>OpenWakeWord</span>
                          <span className="text-[10px] text-muted-foreground">
                            Trained neural models
                          </span>
                        </div>
                      </SelectItem>
                    </SelectContent>
                  </Select>
                </SettingRow>
                {draft.wakeWordEngine === "whisper" && (
                  <SettingRow
                    label="Custom keyword"
                    description="Phrase that triggers wake word"
                  >
                    <Input
                      value={draft.wakeWordCustomKeyword}
                      onChange={(e) =>
                        set("wakeWordCustomKeyword", e.target.value)
                      }
                      className="w-40"
                      placeholder="hey matrix"
                    />
                  </SettingRow>
                )}
                {draft.wakeWordEngine === "oww" && (
                  <>
                    <SettingRow
                      label="OWW model"
                      description="Pre-trained wake word model"
                    >
                      <Select
                        value={draft.wakeWordOwwModel}
                        onValueChange={(v) => set("wakeWordOwwModel", v)}
                      >
                        <SelectTrigger className="w-44">
                          <SelectValue placeholder="Select model..." />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="hey_jarvis">Hey Jarvis</SelectItem>
                          <SelectItem value="hey_mycroft">
                            Hey Mycroft
                          </SelectItem>
                          <SelectItem value="alexa">Alexa</SelectItem>
                          <SelectItem value="hey_rhasspy">
                            Hey Rhasspy
                          </SelectItem>
                          <SelectItem value="timer">Timer</SelectItem>
                          <SelectItem value="weather">Weather</SelectItem>
                        </SelectContent>
                      </Select>
                    </SettingRow>
                    <SliderRow
                      label="Detection threshold"
                      description="Higher = fewer false positives"
                      value={draft.wakeWordOwwThreshold}
                      onChange={(v) => set("wakeWordOwwThreshold", v)}
                      min={0}
                      max={1}
                      step={0.05}
                    />
                  </>
                )}
                <SectionActions
                  section="wakeWord"
                  dirty={sectionDirty.wakeWord}
                  {...sectionActionProps}
                />
              </CardContent>
            </Card>

            {/* ── Scraping ──────────────────────────────────── */}
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Globe className="h-4 w-4" />
                  Scraping
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-1">
                <SettingRow
                  label="Hide scraping browser"
                  description="Run in headless mode — the browser window won't be visible during scraping"
                >
                  <Switch
                    checked={draft.headlessScraping}
                    onCheckedChange={(v) => set("headlessScraping", v)}
                  />
                </SettingRow>
                <ScrapeDelayRow
                  value={draft.scrapeDelay}
                  onChange={(v) => set("scrapeDelay", v)}
                />
                <SectionActions
                  section="scraping"
                  dirty={sectionDirty.scraping}
                  {...sectionActionProps}
                />
              </CardContent>
            </Card>

            {/* ── Proxy & Network ─────────────────────────────── */}
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Network className="h-4 w-4" />
                  Proxy & Network
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-1">
                <SettingRow
                  label="Local proxy enabled"
                  description="HTTP proxy at 127.0.0.1"
                >
                  <Switch
                    checked={draft.proxyEnabled}
                    onCheckedChange={(v) => set("proxyEnabled", v)}
                  />
                </SettingRow>
                <SettingRow label="Proxy port">
                  <NumberInput
                    value={draft.proxyPort}
                    onChange={(v) => set("proxyPort", v)}
                    min={1024}
                    max={65535}
                  />
                </SettingRow>
                <Separator className="my-2" />
                <SettingRow
                  label="Remote tunnel"
                  description="Expose this instance via Cloudflare tunnel"
                >
                  <Switch
                    checked={draft.tunnelEnabled}
                    onCheckedChange={(v) => set("tunnelEnabled", v)}
                  />
                </SettingRow>
                <SectionActions
                  section="proxy"
                  dirty={sectionDirty.proxy}
                  {...sectionActionProps}
                />
              </CardContent>
            </Card>

            {/* ── Notifications ──────────────────────────────── */}
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Bell className="h-4 w-4" />
                  Notifications
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-1">
                <SettingRow
                  label="Sound enabled"
                  description="Play sound on notifications"
                >
                  <Switch
                    checked={draft.notificationSound}
                    onCheckedChange={(v) => set("notificationSound", v)}
                  />
                </SettingRow>
                <SettingRow label="Sound style">
                  <Select
                    value={draft.notificationSoundStyle}
                    onValueChange={(v) =>
                      set(
                        "notificationSoundStyle",
                        v as AppSettings["notificationSoundStyle"],
                      )
                    }
                  >
                    <SelectTrigger className="w-32">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="chime">Chime</SelectItem>
                      <SelectItem value="alert">Alert</SelectItem>
                      <SelectItem value="success">Success</SelectItem>
                      <SelectItem value="error">Error</SelectItem>
                    </SelectContent>
                  </Select>
                </SettingRow>
                <SectionActions
                  section="notifications"
                  dirty={sectionDirty.notifications}
                  {...sectionActionProps}
                />
              </CardContent>
            </Card>
          </div>

          {/* ── Text to Speech (full-width, horizontal) ─────────────── */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-base">
                <Volume2 className="h-4 w-4" />
                Text to Speech
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-5">
              <div>
                <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-3">
                  Defaults
                </p>
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
                  <CompactSelect
                    label="Default voice"
                    value={draft.ttsDefaultVoice || "af_heart"}
                    onValueChange={(v) => set("ttsDefaultVoice", v)}
                    placeholder="Select voice..."
                    loading={catalogs.ttsVoicesLoading}
                  >
                    {catalogs.ttsVoices.length === 0 ? (
                      <div className="py-3 px-2 text-center text-xs text-muted-foreground">
                        {catalogs.ttsVoicesLoading
                          ? "Loading voices…"
                          : "No voices — connect to engine"}
                      </div>
                    ) : (
                      (() => {
                        const groups: Record<
                          string,
                          typeof catalogs.ttsVoices
                        > = {};
                        for (const v of catalogs.ttsVoices) {
                          const lang = v.language;
                          if (!groups[lang]) groups[lang] = [];
                          groups[lang].push(v);
                        }
                        return Object.entries(groups).map(([lang, voices]) => (
                          <SelectGroup key={lang}>
                            <SelectLabel>{lang}</SelectLabel>
                            {voices.map((v) => (
                              <SelectItem key={v.voice_id} value={v.voice_id}>
                                <span className="flex items-center gap-1.5">
                                  {v.name}
                                  <span className="text-[10px] text-muted-foreground">
                                    {v.gender} · {v.quality_grade}
                                  </span>
                                  {v.is_default && (
                                    <span className="text-[10px] font-medium bg-primary/15 text-primary px-1.5 py-0.5 rounded">
                                      Default
                                    </span>
                                  )}
                                </span>
                              </SelectItem>
                            ))}
                          </SelectGroup>
                        ));
                      })()
                    )}
                  </CompactSelect>
                  <CompactSlider
                    label="Default speed"
                    value={draft.ttsDefaultSpeed}
                    onChange={(v) => set("ttsDefaultSpeed", v)}
                    min={0.25}
                    max={4.0}
                    step={0.05}
                  />
                  <CompactNumber
                    label="Streaming threshold"
                    value={draft.ttsStreamingThreshold}
                    onChange={(v) =>
                      set("ttsStreamingThreshold", Math.max(0, v))
                    }
                    min={0}
                    max={5000}
                    step={50}
                  />
                  <CompactSwitch
                    label="Auto-download model"
                    checked={draft.ttsAutoDownloadModel}
                    onCheckedChange={(v) => set("ttsAutoDownloadModel", v)}
                  />
                  <CompactSwitch
                    label="Auto-clean markdown"
                    checked={draft.ttsAutoCleanMarkdown}
                    onCheckedChange={(v) => set("ttsAutoCleanMarkdown", v)}
                  />
                </div>
              </div>

              <Separator />

              <div>
                <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-3">
                  Chat Read-Aloud
                </p>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  <CompactSwitch
                    label="Show read-aloud button"
                    checked={draft.ttsReadAloudEnabled}
                    onCheckedChange={(v) => set("ttsReadAloudEnabled", v)}
                  />
                  <CompactSwitch
                    label="Auto-play responses"
                    checked={draft.ttsReadAloudAutoPlay}
                    onCheckedChange={(v) => set("ttsReadAloudAutoPlay", v)}
                  />
                  <CompactSelect
                    label="Chat voice"
                    value={draft.ttsChatVoice || "__default__"}
                    onValueChange={(v) =>
                      set("ttsChatVoice", v === "__default__" ? "" : v)
                    }
                    placeholder="Same as default"
                  >
                    <SelectItem value="__default__">
                      <span className="text-muted-foreground">
                        Same as default voice
                      </span>
                    </SelectItem>
                    {catalogs.ttsVoices.length > 0 && (
                      <Separator className="my-1" />
                    )}
                    {(() => {
                      const groups: Record<string, typeof catalogs.ttsVoices> =
                        {};
                      for (const v of catalogs.ttsVoices) {
                        const lang = v.language;
                        if (!groups[lang]) groups[lang] = [];
                        groups[lang].push(v);
                      }
                      return Object.entries(groups).map(([lang, voices]) => (
                        <SelectGroup key={lang}>
                          <SelectLabel>{lang}</SelectLabel>
                          {voices.map((v) => (
                            <SelectItem key={v.voice_id} value={v.voice_id}>
                              <span className="flex items-center gap-1.5">
                                {v.name}
                                <span className="text-[10px] text-muted-foreground">
                                  {v.gender}
                                </span>
                              </span>
                            </SelectItem>
                          ))}
                        </SelectGroup>
                      ));
                    })()}
                  </CompactSelect>
                  <CompactSlider
                    label="Chat speed"
                    value={draft.ttsChatSpeed}
                    onChange={(v) => set("ttsChatSpeed", v)}
                    min={0}
                    max={4.0}
                    step={0.05}
                  />
                </div>
              </div>

              <Separator />

              <div>
                <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-3">
                  Notifications
                </p>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  <CompactSelect
                    label="Notification voice"
                    value={draft.ttsNotificationVoice || "__default__"}
                    onValueChange={(v) =>
                      set("ttsNotificationVoice", v === "__default__" ? "" : v)
                    }
                    placeholder="Same as default"
                  >
                    <SelectItem value="__default__">
                      <span className="text-muted-foreground">
                        Same as default voice
                      </span>
                    </SelectItem>
                    {catalogs.ttsVoices.length > 0 && (
                      <Separator className="my-1" />
                    )}
                    {(() => {
                      const groups: Record<string, typeof catalogs.ttsVoices> =
                        {};
                      for (const v of catalogs.ttsVoices) {
                        const lang = v.language;
                        if (!groups[lang]) groups[lang] = [];
                        groups[lang].push(v);
                      }
                      return Object.entries(groups).map(([lang, voices]) => (
                        <SelectGroup key={lang}>
                          <SelectLabel>{lang}</SelectLabel>
                          {voices.map((v) => (
                            <SelectItem key={v.voice_id} value={v.voice_id}>
                              <span className="flex items-center gap-1.5">
                                {v.name}
                                <span className="text-[10px] text-muted-foreground">
                                  {v.gender}
                                </span>
                              </span>
                            </SelectItem>
                          ))}
                        </SelectGroup>
                      ));
                    })()}
                  </CompactSelect>
                </div>
              </div>

              <SectionActions
                section="tts"
                dirty={sectionDirty.tts}
                {...sectionActionProps}
              />
            </CardContent>
          </Card>

          {/* ── LLM Sampling (full-width, horizontal) ───────────────── */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-base">
                <BrainCircuit className="h-4 w-4" />
                LLM Sampling
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-5">
              <div>
                <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-3">
                  Chat
                </p>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  <CompactSlider
                    label="Temperature"
                    value={draft.llmChatTemperature}
                    onChange={(v) => set("llmChatTemperature", v)}
                    min={0}
                    max={2}
                    step={0.05}
                  />
                  <CompactSlider
                    label="Top P"
                    value={draft.llmChatTopP}
                    onChange={(v) => set("llmChatTopP", v)}
                    min={0}
                    max={1}
                    step={0.05}
                  />
                  <CompactNumber
                    label="Top K"
                    value={draft.llmChatTopK}
                    onChange={(v) => set("llmChatTopK", v)}
                    min={1}
                    max={200}
                  />
                  <CompactNumber
                    label="Max tokens"
                    value={draft.llmChatMaxTokens}
                    onChange={(v) => set("llmChatMaxTokens", v)}
                    min={64}
                    max={32768}
                    step={64}
                  />
                </div>
              </div>

              <Separator />

              <div>
                <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-3">
                  Reasoning Mode
                </p>
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
                  <CompactSwitch
                    label="Enable thinking"
                    checked={draft.llmEnableThinking}
                    onCheckedChange={(v) => set("llmEnableThinking", v)}
                  />
                  <CompactSlider
                    label="Temperature"
                    value={draft.llmReasoningTemperature}
                    onChange={(v) => set("llmReasoningTemperature", v)}
                    min={0}
                    max={2}
                    step={0.05}
                  />
                  <CompactSlider
                    label="Top P"
                    value={draft.llmReasoningTopP}
                    onChange={(v) => set("llmReasoningTopP", v)}
                    min={0}
                    max={1}
                    step={0.05}
                  />
                  <CompactNumber
                    label="Top K"
                    value={draft.llmReasoningTopK}
                    onChange={(v) => set("llmReasoningTopK", v)}
                    min={1}
                    max={200}
                  />
                  <CompactNumber
                    label="Max tokens"
                    value={draft.llmReasoningMaxTokens}
                    onChange={(v) => set("llmReasoningMaxTokens", v)}
                    min={64}
                    max={32768}
                    step={64}
                  />
                </div>
              </div>

              <Separator />

              <div>
                <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-3">
                  Tool Calling
                </p>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                  <CompactSlider
                    label="Temperature"
                    value={draft.llmToolCallTemperature}
                    onChange={(v) => set("llmToolCallTemperature", v)}
                    min={0}
                    max={2}
                    step={0.05}
                  />
                  <CompactSlider
                    label="Top P"
                    value={draft.llmToolCallTopP}
                    onChange={(v) => set("llmToolCallTopP", v)}
                    min={0}
                    max={1}
                    step={0.05}
                  />
                  <CompactNumber
                    label="Top K"
                    value={draft.llmToolCallTopK}
                    onChange={(v) => set("llmToolCallTopK", v)}
                    min={1}
                    max={200}
                  />
                </div>
              </div>

              <Separator />

              <div>
                <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-3">
                  Other
                </p>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  <CompactSlider
                    label="Structured output temp"
                    value={draft.llmStructuredOutputTemperature}
                    onChange={(v) => set("llmStructuredOutputTemperature", v)}
                    min={0}
                    max={1}
                    step={0.05}
                  />
                  <CompactNumber
                    label="Stream max tokens"
                    value={draft.llmStreamMaxTokens}
                    onChange={(v) => set("llmStreamMaxTokens", v)}
                    min={64}
                    max={32768}
                    step={64}
                  />
                </div>
              </div>

              <SectionActions
                section="localLlmSampling"
                dirty={sectionDirty.localLlmSampling}
                {...sectionActionProps}
              />
            </CardContent>
          </Card>
        </div>
      </ScrollArea>

      {/* ── Global floating save bar ─────────────────────────────── */}
      {(isGlobalDirty || isSaving) && (
        <div className="border-t bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/75 px-6 py-3 flex items-center justify-between gap-4">
          <p className="text-sm text-muted-foreground">
            {isSaving ? "Saving…" : "You have unsaved changes"}
          </p>
          <div className="flex items-center gap-2">
            {!isSaving && (
              <Button
                variant="ghost"
                size="sm"
                onClick={actions.cancelAll}
                disabled={isSaving}
                className="gap-1.5"
              >
                <X className="h-3.5 w-3.5" />
                Discard all
              </Button>
            )}
            <Button
              size="sm"
              onClick={actions.saveAll}
              disabled={isSaving}
              className="gap-1.5"
            >
              {isSaving ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Save className="h-3.5 w-3.5" />
              )}
              Save all changes
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
