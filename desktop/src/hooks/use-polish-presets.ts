/**
 * usePolishPresets
 *
 * React hook for managing AI Polish presets.
 * Wraps the polish-presets storage layer with local state so components
 * re-render when presets change. Built-in styles are Mandates: their preview
 * text is the resolved Holder's instructions, and `customSuffix` is what the
 * custom-style Holder appends after the person's own text (null until
 * resolved or when it cannot be resolved — never a copy held here).
 */

import { useState, useCallback, useEffect } from "react";
import { resolveLocalMandate } from "@/lib/local-mandates";
import {
  customStyleSuffix,
  getAllPresets,
  getDefaultPresetId,
  setDefaultPresetId,
  saveCustomPreset,
  deleteCustomPreset,
  getPresetById,
  type PolishPreset,
} from "@/lib/polish-presets";

export type { PolishPreset };

export interface UsePolishPresetsReturn {
  presets: PolishPreset[];
  defaultPresetId: string;
  defaultPreset: PolishPreset;
  setDefault: (id: string) => void;
  save: (preset: {
    id?: string;
    name: string;
    systemPrompt: string;
  }) => PolishPreset;
  remove: (id: string) => void;
  refresh: () => void;
  /** What the custom-style Holder appends after the person's text. */
  customSuffix: string | null;
}

export function usePolishPresets(): UsePolishPresetsReturn {
  const [presets, setPresets] = useState<PolishPreset[]>(() => getAllPresets());
  const [defaultPresetId, setDefaultPresetIdState] = useState<string>(() =>
    getDefaultPresetId(),
  );
  const [previews, setPreviews] = useState<Record<string, string>>({});
  const [customSuffix, setCustomSuffix] = useState<string | null>(null);

  // Built-in previews and the custom-style suffix come from the resolved
  // Holders. A failure leaves the preview empty; the run itself resolves
  // again and refuses loudly with the reason.
  useEffect(() => {
    let cancelled = false;
    for (const preset of getAllPresets()) {
      const key = preset.mandateKey;
      if (!key) continue;
      resolveLocalMandate(key)
        .then((holder) => {
          const text = holder.messages.find((m) => m.role === "system")?.content ?? "";
          if (!cancelled) setPreviews((prev) => ({ ...prev, [preset.id]: text }));
        })
        .catch(() => undefined);
    }
    customStyleSuffix()
      .then((suffix) => {
        if (!cancelled) setCustomSuffix(suffix);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const refresh = useCallback(() => {
    setPresets(getAllPresets());
    setDefaultPresetIdState(getDefaultPresetId());
  }, []);

  const setDefault = useCallback((id: string) => {
    setDefaultPresetId(id);
    setDefaultPresetIdState(id);
  }, []);

  const save = useCallback(
    (preset: { id?: string; name: string; systemPrompt: string }) => {
      const saved = saveCustomPreset(preset);
      setPresets(getAllPresets());
      return saved;
    },
    [],
  );

  const remove = useCallback((id: string) => {
    deleteCustomPreset(id);
    setPresets(getAllPresets());
    setDefaultPresetIdState(getDefaultPresetId());
  }, []);

  const withPreview = (p: PolishPreset): PolishPreset =>
    p.isBuiltIn ? { ...p, systemPrompt: previews[p.id] ?? "" } : p;
  const defaultPreset = withPreview(
    getPresetById(defaultPresetId) ?? getPresetById("builtin-standard")!,
  );

  return {
    presets: presets.map(withPreview),
    customSuffix,
    defaultPresetId,
    defaultPreset,
    setDefault,
    save,
    remove,
    refresh,
  };
}
