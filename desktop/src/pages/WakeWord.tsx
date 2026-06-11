/**
 * WakeWord page — dedicated tab for the full wake word system.
 *
 * Sections:
 *   1. Engine selector  — toggle between Whisper-tiny and openWakeWord
 *   2. Live controls    — Listen / Mute / Wake / Dismiss + RMS meter + status
 *   3. Configuration    — keyword (whisper), OWW model / threshold
 *   4. OWW model library — download / manage openWakeWord models
 *   5. Training guide   — step-by-step instructions for custom model
 */

import { useState, useEffect, useCallback } from "react";
import {
  Mic,
  MicOff,
  Volume2,
  Zap,
  Download,
  CheckCircle2,
  AlertCircle,
  Loader2,
  RefreshCw,
  Info,
  ChevronRight,
  Radio,
  Settings2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { engine as engineAPI } from "@/lib/api";
import { isTauri } from "@/lib/sidecar";
import type {
  WakeWordEngine,
  WakeWordSettings,
  OwwModelInfo,
} from "@/lib/transcription/types";
import type {
  WakeWordHookState,
  WakeWordHookActions,
} from "@/hooks/use-wake-word";

// ── Sub-tab bar ───────────────────────────────────────────────────────────────

const INNER_TABS = [
  { value: "controls", label: "Controls", icon: Radio },
  { value: "config", label: "Configuration", icon: Settings2 },
  { value: "models", label: "OWW Models", icon: Download },
] as const;

type InnerTab = (typeof INNER_TABS)[number]["value"];

// ── Props ─────────────────────────────────────────────────────────────────────

interface WakeWordPageProps {
  wwState: WakeWordHookState;
  wwActions: WakeWordHookActions;
}

// ── Main component ────────────────────────────────────────────────────────────

export function WakeWordPage({ wwState, wwActions }: WakeWordPageProps) {
  const [innerTab, setInnerTab] = useState<InnerTab>("controls");
  const [settings, setSettings] = useState<WakeWordSettings>({
    engine: "whisper",
    owwModel: "hey_jarvis",
    owwThreshold: 0.5,
    customKeyword: "hey matrix",
  });
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [settingsError, setSettingsError] = useState<string | null>(null);

  // Load settings on mount
  useEffect(() => {
    if (!isTauri()) return;
    engineAPI
      .getWakeWordSettings()
      .then(setSettings)
      .catch(() => {
        /* engine not yet discovered — use defaults */
      });
  }, []);

  /** Returns true on success so callers can avoid acting on failed saves. */
  const saveSettings = useCallback(
    async (updated: WakeWordSettings): Promise<boolean> => {
      setSettingsSaving(true);
      setSettingsError(null);
      try {
        await engineAPI.saveWakeWordSettings(updated);
        setSettings(updated);
        return true;
      } catch (e) {
        setSettingsError(e instanceof Error ? e.message : String(e));
        return false;
      } finally {
        setSettingsSaving(false);
      }
    },
    [],
  );

  const handleEngineSwitch = useCallback(
    async (e: WakeWordEngine) => {
      const updated = { ...settings, engine: e };
      setSettings(updated);
      await wwActions.setEngine(e);
      await saveSettings(updated);
    },
    [settings, wwActions, saveSettings],
  );

  return (
    <div className="flex h-full flex-col">
      {/* Engine selector banner */}
      <EngineBanner
        engine={settings.engine}
        onSwitch={handleEngineSwitch}
        isListening={wwState.uiMode !== "idle"}
      />

      {/* Inner sub-tabs */}
      <div className="border-b border-border bg-muted/30">
        <nav className="flex gap-0.5 px-4 py-1.5">
          {INNER_TABS.map(({ value, label, icon: Icon }) => (
            <button
              key={value}
              onClick={() => setInnerTab(value)}
              className={cn(
                "flex items-center gap-1.5 rounded px-3 py-1.5 text-sm font-medium transition-colors",
                innerTab === value
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted",
              )}
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
            </button>
          ))}
        </nav>
      </div>

      {/* Error banner (shared) */}
      {(wwState.error || settingsError) && (
        <ErrorBanner
          message={wwState.error ?? settingsError ?? ""}
          onDismiss={() => {
            wwActions.clearError();
            setSettingsError(null);
          }}
        />
      )}

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto">
        {innerTab === "controls" && (
          <ControlsTab
            wwState={wwState}
            wwActions={wwActions}
            engine={settings.engine}
          />
        )}
        {innerTab === "config" && (
          <ConfigTab
            settings={settings}
            onSave={saveSettings}
            saving={settingsSaving}
            engine={settings.engine}
          />
        )}
        {innerTab === "models" && (
          <ModelsTab
            engine={settings.engine}
            currentModel={settings.owwModel}
          />
        )}
      </div>
    </div>
  );
}

// ── Engine selector banner ─────────────────────────────────────────────────────

function EngineBanner({
  engine,
  onSwitch,
  isListening,
}: {
  engine: WakeWordEngine;
  onSwitch: (e: WakeWordEngine) => Promise<void>;
  isListening: boolean;
}) {
  const [switching, setSwitching] = useState(false);

  const handleSwitch = async (e: WakeWordEngine) => {
    setSwitching(true);
    try {
      await onSwitch(e);
    } finally {
      setSwitching(false);
    }
  };

  return (
    <div className="border-b border-border bg-muted/20 px-4 py-3">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Detection Engine
          </p>
          <p className="mt-0.5 text-xs text-muted-foreground/70">
            {isListening && (
              <span className="text-yellow-500">
                Stop listening to switch engines.&ensp;
              </span>
            )}
            Changes take effect immediately.
          </p>
        </div>
        <div className="flex items-center gap-1 rounded-lg border border-border bg-background p-0.5">
          <EngineButton
            active={engine === "whisper"}
            onClick={() => !isListening && void handleSwitch("whisper")}
            disabled={switching || isListening}
            label="Whisper-tiny"
            sublabel="Built-in · 2s latency"
          />
          <EngineButton
            active={engine === "oww"}
            onClick={() => !isListening && void handleSwitch("oww")}
            disabled={switching || isListening}
            label="openWakeWord"
            sublabel="ONNX · ~150ms latency"
          />
        </div>
      </div>
    </div>
  );
}

function EngineButton({
  active,
  onClick,
  disabled,
  label,
  sublabel,
}: {
  active: boolean;
  onClick: () => void;
  disabled: boolean;
  label: string;
  sublabel: string;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "flex flex-col items-start rounded px-3 py-1.5 text-left transition-colors",
        active
          ? "bg-primary text-primary-foreground shadow-sm"
          : "text-muted-foreground hover:text-foreground hover:bg-muted disabled:opacity-50 disabled:cursor-not-allowed",
      )}
    >
      <span className="text-sm font-medium">{label}</span>
      <span
        className={cn(
          "text-xs",
          active ? "text-primary-foreground/70" : "text-muted-foreground/70",
        )}
      >
        {sublabel}
      </span>
    </button>
  );
}

// ── Controls tab ──────────────────────────────────────────────────────────────

function ControlsTab({
  wwState,
  wwActions,
  engine,
}: {
  wwState: WakeWordHookState;
  wwActions: WakeWordHookActions;
  engine: WakeWordEngine;
}) {
  const { uiMode, listenRms, kmsModelReady } = wwState;

  const statusConfig: Record<
    typeof uiMode,
    { label: string; color: string; dot: string }
  > = {
    idle: {
      label: "Not listening",
      color: "text-muted-foreground",
      dot: "bg-muted-foreground/40",
    },
    setup: {
      label: "Starting up…",
      color: "text-yellow-500",
      dot: "bg-yellow-500 animate-pulse",
    },
    listening: {
      label: "Listening for wake word",
      color: "text-green-500",
      dot: "bg-green-500 animate-pulse",
    },
    muted: {
      label: "Muted",
      color: "text-muted-foreground",
      dot: "bg-yellow-500/60",
    },
    dismissed: {
      label: "Dismissed — resumes in 10 s",
      color: "text-yellow-500",
      dot: "bg-yellow-500 animate-pulse",
    },
    active: {
      label: "Awake — transcribing",
      color: "text-sky-400",
      dot: "bg-sky-400 animate-ping",
    },
  };

  const { label, color, dot } = statusConfig[uiMode];
  const isIdle = uiMode === "idle";
  const isListening =
    uiMode === "listening" || uiMode === "active" || uiMode === "dismissed";
  const isMuted = uiMode === "muted";

  const notReady = engine === "whisper" && !kmsModelReady && uiMode === "idle";

  return (
    <div className="space-y-6 p-6">
      {/* Status card */}
      <div className="rounded-xl border border-border bg-muted/20 p-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span
              className={cn("inline-block h-2.5 w-2.5 rounded-full", dot)}
            />
            <span className={cn("text-sm font-medium", color)}>{label}</span>
          </div>
          {/* RMS bar — visible while listening */}
          {uiMode !== "idle" && <RmsBar rms={listenRms} />}
        </div>

        {/* Engine badge */}
        <div className="mt-3 flex items-center gap-2">
          <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
            {engine === "whisper"
              ? "Whisper-tiny engine"
              : "openWakeWord engine"}
          </span>
          {engine === "whisper" && (
            <span
              className={cn(
                "rounded-full px-2 py-0.5 text-xs",
                kmsModelReady
                  ? "bg-green-500/10 text-green-500"
                  : "bg-yellow-500/10 text-yellow-500",
              )}
            >
              {kmsModelReady ? "Model ready" : "Model not downloaded"}
            </span>
          )}
        </div>
      </div>

      {/* Not-ready warning for whisper */}
      {notReady && (
        <div className="flex items-start gap-3 rounded-lg border border-yellow-500/30 bg-yellow-500/5 p-4">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-yellow-500" />
          <div>
            <p className="text-sm font-medium text-yellow-500">
              Voice setup required
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              Complete Speech to Text → Setup → Quick Setup to download
              ggml-tiny.en.bin before enabling wake word detection.
            </p>
          </div>
        </div>
      )}

      {/* Primary controls */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {/* Listen / Stop */}
        {isIdle ? (
          <CtrlBtn
            icon={<Mic className="h-4 w-4" />}
            label="Listen"
            variant="accent"
            disabled={notReady}
            onClick={() => void wwActions.setup()}
          />
        ) : (
          <CtrlBtn
            icon={<MicOff className="h-4 w-4" />}
            label="Stop"
            variant="destructive"
            onClick={() => void wwActions.stopListening()}
          />
        )}

        {/* Mute / Unmute */}
        {isListening && (
          <CtrlBtn
            icon={<MicOff className="h-4 w-4" />}
            label="Mute"
            onClick={() => void wwActions.mute()}
          />
        )}
        {isMuted && (
          <CtrlBtn
            icon={<Mic className="h-4 w-4" />}
            label="Resume"
            variant="accent"
            onClick={() => void wwActions.unmute()}
          />
        )}

        {/* Manual wake */}
        {!isIdle && (
          <CtrlBtn
            icon={<Zap className="h-4 w-4" />}
            label="Wake now"
            variant="secondary"
            onClick={() => void wwActions.manualTrigger()}
          />
        )}

        {/* Dismiss */}
        {uiMode === "active" && (
          <CtrlBtn
            icon={<MicOff className="h-4 w-4" />}
            label="Not for me"
            variant="destructive"
            onClick={() => void wwActions.dismiss()}
          />
        )}
      </div>

      {/* How it works */}
      <HowItWorks engine={engine} />
    </div>
  );
}

// ── RMS bar ───────────────────────────────────────────────────────────────────

function RmsBar({ rms }: { rms: number }) {
  const pct = Math.min(rms * 100, 100);
  return (
    <div className="flex items-center gap-2">
      <Volume2 className="h-3.5 w-3.5 text-muted-foreground" />
      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-green-500 transition-all duration-75"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

// ── Config tab ────────────────────────────────────────────────────────────────

function ConfigTab({
  settings,
  onSave,
  saving,
  engine,
}: {
  settings: WakeWordSettings;
  onSave: (s: WakeWordSettings) => Promise<boolean>;
  saving: boolean;
  engine: WakeWordEngine;
}) {
  const [local, setLocal] = useState(settings);
  const [dirty, setDirty] = useState(false);

  // Sync with parent when settings load
  useEffect(() => {
    setLocal(settings);
    setDirty(false);
  }, [settings]);

  const update = <K extends keyof WakeWordSettings>(
    k: K,
    v: WakeWordSettings[K],
  ) => {
    setLocal((prev) => ({ ...prev, [k]: v }));
    setDirty(true);
  };

  const handleSave = async () => {
    const ok = await onSave(local);
    // On failure, keep the form dirty and skip pushing config to the engine —
    // the parent surfaces the error banner.
    if (!ok) return;
    setDirty(false);
    // If OWW engine is running, push new config to it
    if (engine === "oww") {
      try {
        await engineAPI.owwConfigure({
          modelName: local.owwModel,
          threshold: local.owwThreshold,
        });
      } catch {
        /* non-critical */
      }
    } else {
      // Push custom keyword to Rust engine
      try {
        const { invoke: tauriInvoke } = await import("@tauri-apps/api/core");
        await tauriInvoke("configure_wake_word", {
          keyword: local.customKeyword,
        });
      } catch {
        /* non-critical */
      }
    }
  };

  return (
    <div className="space-y-6 p-6">
      {/* Whisper engine config */}
      {engine === "whisper" && (
        <Section
          title="Whisper Engine"
          description="Keyword matched against Whisper transcription output."
        >
          <Field
            label="Wake keyword"
            description="Case-insensitive substring match on Whisper's output. Use real English words."
          >
            <input
              type="text"
              value={local.customKeyword}
              onChange={(e) => update("customKeyword", e.target.value)}
              placeholder="hey matrix"
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </Field>
          <div className="rounded-lg border border-blue-500/20 bg-blue-500/5 p-3 text-xs text-muted-foreground">
            <strong className="text-blue-400">Tip:</strong> Whisper transcribes
            "matrx" as "matrix" — use the common spelling for reliable
            detection. Avoid made-up words or unusual proper nouns.
          </div>
        </Section>
      )}

      {/* OWW engine config */}
      {engine === "oww" && (
        <>
          <Section
            title="openWakeWord Engine"
            description="ONNX-based keyword spotter — faster and more accurate than Whisper for fixed phrases."
          >
            <Field
              label="Active model"
              description="The .onnx model file to use for detection."
            >
              <OwwModelPicker
                current={local.owwModel}
                onChange={(v) => update("owwModel", v)}
              />
            </Field>

            <Field
              label={`Detection threshold: ${local.owwThreshold.toFixed(2)}`}
              description="Higher = fewer false positives but may miss soft speech. Default: 0.50"
            >
              <input
                type="range"
                min={0.1}
                max={0.99}
                step={0.01}
                value={local.owwThreshold}
                onChange={(e) =>
                  update("owwThreshold", parseFloat(e.target.value))
                }
                className="w-full accent-primary"
              />
              <div className="mt-1 flex justify-between text-xs text-muted-foreground">
                <span>0.10 (sensitive)</span>
                <span>0.50 (balanced)</span>
                <span>0.99 (strict)</span>
              </div>
            </Field>
          </Section>
        </>
      )}

      {/* Save button */}
      <div className="flex items-center gap-3">
        <Button
          onClick={handleSave}
          disabled={!dirty || saving}
          size="sm"
          className="gap-2"
        >
          {saving ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <CheckCircle2 className="h-3.5 w-3.5" />
          )}
          Save
        </Button>
        {!dirty && !saving && (
          <span className="text-xs text-muted-foreground">Settings saved</span>
        )}
      </div>
    </div>
  );
}

// ── OWW model picker (inline select in Config tab) ─────────────────────────────

function OwwModelPicker({
  current,
  onChange,
}: {
  current: string;
  onChange: (v: string) => void;
}) {
  const [models, setModels] = useState<OwwModelInfo[]>([]);

  useEffect(() => {
    if (!isTauri()) return;
    engineAPI
      .owwListModels()
      .then((r) => setModels(r.models))
      .catch((e) => console.warn("[wake-word] owwListModels failed:", e));
  }, []);

  const downloaded = models.filter((m) => m.downloaded);
  if (downloaded.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No models downloaded yet. Go to the <strong>OWW Models</strong> tab.
      </p>
    );
  }

  return (
    <select
      value={current}
      onChange={(e) => onChange(e.target.value)}
      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
    >
      {downloaded.map((m) => (
        <option key={m.name} value={m.name}>
          {m.name}
          {m.is_custom ? " (custom)" : ""}
        </option>
      ))}
    </select>
  );
}

// ── Models tab ────────────────────────────────────────────────────────────────

function ModelsTab({
  engine,
  currentModel,
}: {
  engine: WakeWordEngine;
  currentModel: string;
}) {
  const [models, setModels] = useState<OwwModelInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [downloading, setDownloading] = useState<Set<string>>(new Set());
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    if (!isTauri()) {
      // Outside Tauri there is nothing to list — clear the initial spinner.
      setLoading(false);
      return;
    }
    setLoading(true);
    engineAPI
      .owwListModels()
      .then((r) => setModels(r.models))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const handleDownload = useCallback(
    async (name: string) => {
      setDownloadError(null);
      setDownloading((prev) => new Set([...prev, name]));
      try {
        await engineAPI.owwDownloadModel(name);
        refresh();
      } catch (e) {
        setDownloadError(
          `Failed to download ${name}: ${e instanceof Error ? e.message : String(e)}`,
        );
      } finally {
        setDownloading((prev) => {
          const next = new Set(prev);
          next.delete(name);
          return next;
        });
      }
    },
    [refresh],
  );

  if (engine === "whisper") {
    return (
      <div className="p-6">
        <div className="flex items-start gap-3 rounded-lg border border-border bg-muted/20 p-4">
          <Info className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">
            The Whisper engine reuses the <strong>ggml-tiny.en.bin</strong>{" "}
            model you already downloaded during Voice Setup — no separate models
            are needed. Switch to the <strong>openWakeWord</strong> engine above
            to use dedicated wake word models.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold">openWakeWord Models</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Each model is ~3 MB and downloaded to{" "}
            <code className="text-xs">~/.matrx/oww_models/</code>
          </p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={refresh}
          disabled={loading}
          className="gap-1.5"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
          Refresh
        </Button>
      </div>

      {downloadError && (
        <ErrorBanner
          message={downloadError}
          onDismiss={() => setDownloadError(null)}
        />
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading models…
        </div>
      ) : (
        <div className="space-y-2">
          {models.map((model) => (
            <ModelCard
              key={model.name}
              model={model}
              isActive={model.name === currentModel}
              isDownloading={downloading.has(model.name)}
              onDownload={() => void handleDownload(model.name)}
            />
          ))}
        </div>
      )}

      {/* Custom model hint */}
      <div className="rounded-lg border border-border bg-muted/10 p-4 text-xs text-muted-foreground">
        <strong className="text-foreground">Custom models:</strong> Place any{" "}
        <code>.onnx</code> file in <code>~/.matrx/oww_models/</code> and it will
        appear here automatically after refreshing. See the{" "}
        <strong>Training Guide</strong> tab to train your own "hey matrix"
        model.
      </div>
    </div>
  );
}

function ModelCard({
  model,
  isActive,
  isDownloading,
  onDownload,
}: {
  model: OwwModelInfo;
  isActive: boolean;
  isDownloading: boolean;
  onDownload: () => void;
}) {
  return (
    <div
      className={cn(
        "flex items-center gap-3 rounded-lg border p-3 transition-colors",
        isActive
          ? "border-primary/40 bg-primary/5"
          : "border-border bg-muted/10 hover:bg-muted/20",
      )}
    >
      {/* Icon */}
      <div
        className={cn(
          "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg",
          model.downloaded
            ? "bg-green-500/10 text-green-500"
            : "bg-muted text-muted-foreground",
        )}
      >
        {model.downloaded ? (
          <CheckCircle2 className="h-4 w-4" />
        ) : (
          <Download className="h-4 w-4" />
        )}
      </div>

      {/* Info */}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="truncate text-sm font-medium">{model.name}</p>
          {isActive && (
            <span className="shrink-0 rounded-full bg-primary/20 px-1.5 py-0.5 text-xs text-primary">
              active
            </span>
          )}
          {model.is_custom && (
            <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
              custom
            </span>
          )}
        </div>
        <p className="mt-0.5 truncate text-xs text-muted-foreground">
          {model.description}
        </p>
        <p className="mt-0.5 text-xs text-muted-foreground/60">
          {model.size_mb} MB
        </p>
      </div>

      {/* Action */}
      {!model.downloaded && (
        <Button
          size="sm"
          variant="outline"
          onClick={onDownload}
          disabled={isDownloading}
          className="shrink-0 gap-1.5"
        >
          {isDownloading ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> Downloading
            </>
          ) : (
            <>
              <Download className="h-3.5 w-3.5" /> Download
            </>
          )}
        </Button>
      )}
    </div>
  );
}

// ── How it works info box ─────────────────────────────────────────────────────

function HowItWorks({ engine }: { engine: WakeWordEngine }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg border border-border bg-muted/10">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-4 py-3 text-left text-xs text-muted-foreground hover:text-foreground transition-colors"
      >
        <span className="flex items-center gap-1.5">
          <Info className="h-3.5 w-3.5" />
          How does{" "}
          {engine === "whisper" ? "the Whisper engine" : "openWakeWord"} work?
        </span>
        <ChevronRight
          className={cn(
            "h-3.5 w-3.5 transition-transform",
            open && "rotate-90",
          )}
        />
      </button>
      {open && (
        <div className="border-t border-border px-4 py-3 text-xs text-muted-foreground space-y-2">
          {engine === "whisper" ? (
            <>
              <p>
                The Whisper-tiny model listens in{" "}
                <strong>2-second windows</strong>. Every 2 seconds it
                transcribes the captured audio to text and checks whether that
                text contains the configured keyword (case-insensitive substring
                match).
              </p>
              <p>
                <strong>Latency:</strong> up to 2 seconds from speaking the wake
                word to detection.
                <br />
                <strong>Model size:</strong> 75 MB (ggml-tiny.en.bin — already
                downloaded for Voice).
                <br />
                <strong>False positives:</strong> any phrase that transcribes to
                contain the keyword string.
              </p>
            </>
          ) : (
            <>
              <p>
                openWakeWord runs a tiny ONNX neural network on{" "}
                <strong>80 ms audio frames</strong>
                continuously, producing a confidence score 12× per second. When
                the score exceeds the threshold, the wake word fires.
              </p>
              <p>
                <strong>Latency:</strong> ~80–160 ms — you hear the beep before
                you finish saying the phrase.
                <br />
                <strong>Model size:</strong> ~3 MB per wake word (dedicated ONNX
                classifier).
                <br />
                <strong>CPU usage:</strong> minimal — designed to run
                permanently in the background.
              </p>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ── Small shared components ───────────────────────────────────────────────────

function CtrlBtn({
  icon,
  label,
  onClick,
  disabled = false,
  variant = "outline",
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  disabled?: boolean;
  variant?: "outline" | "accent" | "destructive" | "secondary";
}) {
  const base =
    "flex items-center justify-center gap-2 rounded-lg border px-4 py-2.5 text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed";
  const styles: Record<typeof variant, string> = {
    outline: "border-border bg-muted/20 text-foreground hover:bg-muted",
    accent: "border-primary/40 bg-primary/10 text-primary hover:bg-primary/20",
    destructive:
      "border-destructive/40 bg-destructive/10 text-destructive hover:bg-destructive/20",
    secondary: "border-border bg-muted/20 text-foreground hover:bg-muted",
  };
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={cn(base, styles[variant])}
    >
      {icon}
      {label}
    </button>
  );
}

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-3">
      <div>
        <h3 className="text-sm font-semibold">{title}</h3>
        {description && (
          <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
        )}
      </div>
      {children}
    </div>
  );
}

function Field({
  label,
  description,
  children,
}: {
  label: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <label className="text-xs font-medium">{label}</label>
      {description && (
        <p className="text-xs text-muted-foreground">{description}</p>
      )}
      {children}
    </div>
  );
}

function ErrorBanner({
  message,
  onDismiss,
}: {
  message: string;
  onDismiss: () => void;
}) {
  return (
    <div className="flex items-start gap-3 border-b border-destructive/20 bg-destructive/5 px-4 py-3">
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
      <p className="flex-1 text-xs text-destructive">{message}</p>
      <button
        onClick={onDismiss}
        className="text-xs text-muted-foreground hover:text-foreground"
      >
        Dismiss
      </button>
    </div>
  );
}
