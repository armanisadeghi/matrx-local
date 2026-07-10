/**
 * WorkflowSection — the full "Quick Workflows" experience for image
 * generation, mounted as its own top-level tab.
 *
 * Self-sufficient: reads everything from MediaGenContext (useMediaGenApp).
 * Each preset renders as a card (name, description, suggested model,
 * generation settings); the user picks a preset, types a subject, optionally
 * overrides the model and seed, and generates.  The result reuses the shared
 * GeneratedImageView (seed + saved-path surfaced), same as the Images tab.
 */

import { useState, useMemo, useCallback } from "react";
import { AlertCircle, Loader2, RefreshCw, Sparkles, Wand2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import type { ImageGenWorkflowPreset } from "@/lib/api";
import {
  ErrorNote,
  GeneratedImageView,
  SeedInput,
  parseSeedText,
  randomSeed,
} from "./shared";

/** Sentinel for "use the preset's suggested model" in the override select. */
const SUGGESTED = "__suggested__";

function PresetCard({
  preset,
  selected,
  onSelect,
}: {
  preset: ImageGenWorkflowPreset;
  selected: boolean;
  onSelect: (presetId: string) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect(preset.preset_id)}
      className={`rounded-lg border bg-card p-4 text-left space-y-2 transition-colors ${
        selected
          ? "border-violet-500 bg-violet-500/5"
          : "hover:bg-muted/10"
      }`}
      aria-pressed={selected}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="font-medium text-sm">{preset.name}</p>
        {selected && <Sparkles className="h-4 w-4 text-violet-500 shrink-0" />}
      </div>
      <p className="text-xs text-muted-foreground leading-relaxed">
        {preset.description}
      </p>
      <div className="flex flex-wrap gap-1.5 text-[10px]">
        <span className="rounded bg-muted px-1.5 py-0.5">
          Suggested: {preset.suggested_model_id}
        </span>
        <span className="rounded bg-muted px-1.5 py-0.5 tabular-nums">
          {preset.width}×{preset.height} · {preset.steps} steps · guidance{" "}
          {preset.guidance}
        </span>
      </div>
    </button>
  );
}

export function WorkflowSection() {
  const [state, actions] = useMediaGenApp();
  const {
    imageStatus,
    imageModels,
    imagePresets,
    imageStatusLoading,
    imageStatusError,
    imageGenerating,
    imageGenError,
    imageResult,
  } = state;
  const {
    refreshImage,
    generateImageWorkflow,
    clearImageResult,
    clearImageGenError,
  } = actions;

  // Transient form state — intentionally local.
  const [presetId, setPresetId] = useState<string>("");
  const [subject, setSubject] = useState("");
  const [modelOverride, setModelOverride] = useState<string>(SUGGESTED);
  const [seedText, setSeedText] = useState("");

  const preset = useMemo(
    () => imagePresets.find((p) => p.preset_id === presetId) ?? null,
    [imagePresets, presetId],
  );
  const resolvedPrompt = preset
    ? preset.prompt_template.replace("{subject}", subject || "…")
    : "";

  const handleGenerate = useCallback(async () => {
    if (!presetId || !subject.trim()) return;
    // Resolve a concrete seed client-side even for "random" so every result
    // is reproducible (the used seed is shown on the result).
    const seed = parseSeedText(seedText) ?? randomSeed();
    await generateImageWorkflow({
      preset_id: presetId,
      subject: subject.trim(),
      model_id: modelOverride === SUGGESTED ? undefined : modelOverride,
      seed,
    });
  }, [presetId, subject, modelOverride, seedText, generateImageWorkflow]);

  // ── Gates ────────────────────────────────────────────────────────────────
  if (imageStatusLoading && !imageStatus) {
    return (
      <div className="flex items-center justify-center py-20 gap-3 text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
        <span className="text-sm">Checking image generation status…</span>
      </div>
    );
  }

  if (imageStatusError) {
    return (
      <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-4 space-y-3">
        <div className="flex items-start gap-3">
          <AlertCircle className="h-4 w-4 text-destructive mt-0.5 shrink-0" />
          <div className="text-sm space-y-1.5 min-w-0">
            <p className="font-medium text-foreground">
              Could not load workflows
            </p>
            <p className="text-muted-foreground text-xs leading-relaxed break-all">
              {imageStatusError}
            </p>
          </div>
        </div>
        <Button size="sm" variant="secondary" onClick={() => void refreshImage()}>
          <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
          Try again
        </Button>
      </div>
    );
  }

  if (imageStatus && !imageStatus.available) {
    return (
      <div className="rounded-xl border px-5 py-8 flex flex-col items-center text-center gap-3">
        <div className="rounded-lg bg-muted p-3">
          <Wand2 className="h-6 w-6 text-muted-foreground" />
        </div>
        <p className="font-semibold text-sm">
          Workflows need image generation set up first
        </p>
        <p className="text-xs text-muted-foreground leading-relaxed max-w-md">
          {imageStatus.unavailable_reason ??
            "The on-device AI packages are not installed yet."}{" "}
          Open the Images tab to run the one-time setup, then come back here.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6 pb-8">
      <div className="space-y-1">
        <h3 className="text-sm font-semibold flex items-center gap-2">
          <Wand2 className="h-4 w-4 text-violet-500" />
          Quick workflows
        </h3>
        <p className="text-xs text-muted-foreground">
          Pre-built prompt recipes — pick one, describe your subject, and
          generate. Every workflow shows exactly which model and settings it
          uses.
        </p>
      </div>

      {imagePresets.length === 0 ? (
        <div className="rounded-lg border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
          No workflow presets available from the engine.
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {imagePresets.map((p) => (
            <PresetCard
              key={p.preset_id}
              preset={p}
              selected={p.preset_id === presetId}
              onSelect={setPresetId}
            />
          ))}
        </div>
      )}

      {preset && (
        <div className="space-y-4 max-w-xl">
          <div className="space-y-1.5">
            <Label className="text-xs">Subject / topic</Label>
            <Input
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              placeholder={
                preset.name === "Photorealistic Portrait"
                  ? "a smiling woman in business attire"
                  : "describe your subject…"
              }
              className="text-sm"
            />
          </div>

          {subject && (
            <div className="rounded-lg border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
              <span className="font-medium text-foreground">
                Generated prompt:{" "}
              </span>
              {resolvedPrompt}
              {preset.negative_prompt && (
                <p className="mt-1">
                  <span className="font-medium text-foreground">
                    Negative prompt:{" "}
                  </span>
                  {preset.negative_prompt}
                </p>
              )}
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label className="text-xs">Model</Label>
              <Select value={modelOverride} onValueChange={setModelOverride}>
                <SelectTrigger className="text-sm">
                  <SelectValue placeholder="Model" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={SUGGESTED}>
                    Suggested ({preset.suggested_model_id})
                  </SelectItem>
                  {imageModels.map((m) => (
                    <SelectItem key={m.model_id} value={m.model_id}>
                      {m.name}
                      {!m.is_downloaded ? " (not downloaded)" : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">
                Seed{" "}
                <span className="text-muted-foreground">
                  (blank = random; shown on the result)
                </span>
              </Label>
              <SeedInput value={seedText} onChange={setSeedText} />
            </div>
          </div>

          {imageStatus?.loaded_model_id && (
            <p className="text-[11px] text-muted-foreground">
              Currently loaded model:{" "}
              <span className="font-medium text-foreground">
                {imageStatus.loaded_model_id}
              </span>
            </p>
          )}

          {imageGenError && (
            <ErrorNote message={imageGenError} onDismiss={clearImageGenError} />
          )}

          <Button
            className="w-full"
            disabled={imageGenerating || !presetId || !subject.trim()}
            onClick={() => void handleGenerate()}
          >
            {imageGenerating ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
                Generating…
              </>
            ) : (
              <>
                <Wand2 className="h-4 w-4 mr-2" />
                Generate Image
              </>
            )}
          </Button>
        </div>
      )}

      {imageResult && (
        <div className="max-w-xl">
          <GeneratedImageView
            result={imageResult}
            onClear={clearImageResult}
            onReuseSeed={(s) => setSeedText(String(s))}
          />
        </div>
      )}
    </div>
  );
}
