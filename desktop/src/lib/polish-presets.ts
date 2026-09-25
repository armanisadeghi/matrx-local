/**
 * polish-presets.ts
 *
 * The Voice page's AI Polish styles.
 *
 * BUILT-IN styles are Mandates (`local.polish_style_*`): the platform's Holder
 * for each carries its instructions, user template, output schema and
 * settings — there is no style prompt in this file. A CUSTOM style is the
 * person's own text, stored in localStorage under "matrx-polish-presets", and
 * runs through `local.polish_style_custom`, whose Holder takes that text as the
 * `style_instructions` variable and adds the platform's JSON-output
 * instruction itself.
 */

import {
  LOCAL_MODEL_MANDATE_KEYS,
  resolveLocalMandate,
  type LocalModelMandateKey,
} from "@/lib/local-mandates";

export interface PolishPreset {
  id: string;
  /** Display name shown in the dropdown. */
  name: string;
  /**
   * Built-in styles: the Mandate whose Holder IS this style. Absent for a
   * custom style (it runs through `local.polish_style_custom`).
   */
  mandateKey?: LocalModelMandateKey;
  /**
   * Custom styles: the person's own style instructions. Built-in styles: a
   * read-only preview of the Holder's instructions once resolved ("" before).
   */
  systemPrompt: string;
  /** True for shipped styles, which cannot be edited or deleted. */
  isBuiltIn: boolean;
  /** ISO timestamp of last save — used for ordering custom presets. */
  updatedAt: string;
}

// ── Storage key ───────────────────────────────────────────────────────────

const STORAGE_KEY = "matrx-polish-presets";
const DEFAULT_PRESET_KEY = "matrx-polish-default-preset";

// ── Built-in styles (each one a Mandate) ──────────────────────────────────

const SHIPPED = "2025-01-01T00:00:00.000Z";

export const BUILT_IN_PRESETS: PolishPreset[] = [
  { id: "builtin-standard", name: "Standard Clean-up", mandateKey: LOCAL_MODEL_MANDATE_KEYS.polishStyleStandard },
  { id: "builtin-formal", name: "Formal / Professional", mandateKey: LOCAL_MODEL_MANDATE_KEYS.polishStyleFormal },
  { id: "builtin-bullets", name: "Bullet Points", mandateKey: LOCAL_MODEL_MANDATE_KEYS.polishStyleBullets },
  { id: "builtin-action-items", name: "Action Items", mandateKey: LOCAL_MODEL_MANDATE_KEYS.polishStyleActionItems },
  { id: "builtin-meeting", name: "Meeting Notes", mandateKey: LOCAL_MODEL_MANDATE_KEYS.polishStyleMeetingNotes },
  { id: "builtin-verbatim", name: "Light Cleanup Only", mandateKey: LOCAL_MODEL_MANDATE_KEYS.polishStyleLightCleanup },
].map((p) => ({ ...p, systemPrompt: "", isBuiltIn: true, updatedAt: SHIPPED }));

// ── Custom-style Holder (the platform's JSON-output instruction) ─────────

const STYLE_VARIABLE = "{{style_instructions}}";

/**
 * What the custom-style Holder appends after the person's text — read from
 * the resolved Holder, never written here. Null when its system message does
 * not carry the `{{style_instructions}}` slot.
 */
export async function customStyleSuffix(): Promise<string | null> {
  const holder = await resolveLocalMandate(LOCAL_MODEL_MANDATE_KEYS.polishStyleCustom);
  const system = holder.messages.find((m) => m.role === "system")?.content ?? "";
  const at = system.indexOf(STYLE_VARIABLE);
  return at === -1 ? null : system.slice(at + STYLE_VARIABLE.length);
}

/**
 * The person's own text of a custom style. Styles saved before 2026-09-25
 * carried the platform's JSON instruction appended to them; the Holder adds it
 * now, so a saved copy is removed here (it is the Holder's text, not theirs).
 */
export function personStyleText(saved: string, suffix: string | null): string {
  if (suffix && suffix.trim() && saved.endsWith(suffix)) {
    return saved.slice(0, saved.length - suffix.length).trimEnd();
  }
  return saved;
}

/** The Mandate and variables one polish run uses for `preset`. */
export async function polishRunFor(
  preset: PolishPreset,
  transcript: string,
): Promise<{ mandateKey: LocalModelMandateKey; vars: Record<string, string> }> {
  if (preset.mandateKey) {
    return { mandateKey: preset.mandateKey, vars: { transcript } };
  }
  const suffix = await customStyleSuffix();
  return {
    mandateKey: LOCAL_MODEL_MANDATE_KEYS.polishStyleCustom,
    vars: { transcript, style_instructions: personStyleText(preset.systemPrompt, suffix) },
  };
}

// ── Storage helpers ───────────────────────────────────────────────────────

function readCustomPresets(): PolishPreset[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as PolishPreset[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function writeCustomPresets(presets: PolishPreset[]): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(presets));
}

// ── Public API ────────────────────────────────────────────────────────────

/** Returns built-ins first, then custom presets sorted by updatedAt desc. */
export function getAllPresets(): PolishPreset[] {
  const custom = readCustomPresets().sort(
    (a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime(),
  );
  return [...BUILT_IN_PRESETS, ...custom];
}

export function getPresetById(id: string): PolishPreset | undefined {
  return getAllPresets().find((p) => p.id === id);
}

export function saveCustomPreset(preset: {
  id?: string;
  name: string;
  systemPrompt: string;
}): PolishPreset {
  const custom = readCustomPresets();
  const now = new Date().toISOString();

  if (preset.id) {
    // Update existing
    const idx = custom.findIndex((p) => p.id === preset.id);
    const updated: PolishPreset = {
      id: preset.id,
      name: preset.name,
      systemPrompt: preset.systemPrompt,
      isBuiltIn: false,
      updatedAt: now,
    };
    if (idx >= 0) {
      custom[idx] = updated;
    } else {
      custom.push(updated);
    }
    writeCustomPresets(custom);
    return updated;
  } else {
    // Create new
    const newPreset: PolishPreset = {
      id: `custom-${Date.now()}`,
      name: preset.name,
      systemPrompt: preset.systemPrompt,
      isBuiltIn: false,
      updatedAt: now,
    };
    custom.push(newPreset);
    writeCustomPresets(custom);
    return newPreset;
  }
}

export function deleteCustomPreset(id: string): void {
  const custom = readCustomPresets().filter((p) => p.id !== id);
  writeCustomPresets(custom);
  // If it was the default, clear default so it falls back to built-in
  if (getDefaultPresetId() === id) {
    localStorage.removeItem(DEFAULT_PRESET_KEY);
  }
}

export function getDefaultPresetId(): string {
  return localStorage.getItem(DEFAULT_PRESET_KEY) ?? "builtin-standard";
}

export function setDefaultPresetId(id: string): void {
  localStorage.setItem(DEFAULT_PRESET_KEY, id);
}
