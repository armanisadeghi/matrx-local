import type { ImageGenLoraInfo, ImageGenModelInfo } from "@/lib/api";

export type LoraCompatibility = "compatible" | "incompatible" | "unknown";

const FAMILY_ALIASES: Record<string, string> = {
  flux2klein: "flux2",
  flux2: "flux2",
  flux: "flux",
  zimage: "zimage",
  zimageturbo: "zimage",
  stablediffusionxl: "sdxl",
  sdxl: "sdxl",
  stablediffusion: "sd15",
  sd15: "sd15",
};

export function normalizeLoraFamily(value: string | null | undefined): string {
  if (!value) return "unknown";
  const compact = value.toLowerCase().replace(/[^a-z0-9]/g, "");
  if (!compact || compact === "unknown") return "unknown";
  return FAMILY_ALIASES[compact] ?? compact;
}

export function modelLoraFamily(
  model: Pick<ImageGenModelInfo, "pipeline_type" | "lora_family"> | null | undefined,
): string {
  return normalizeLoraFamily(model?.lora_family ?? model?.pipeline_type);
}

export function classifyLoraCompatibility(
  baseFamily: string | null | undefined,
  model: Pick<ImageGenModelInfo, "pipeline_type" | "lora_family"> | null | undefined,
): LoraCompatibility {
  const loraFamily = normalizeLoraFamily(baseFamily);
  const targetFamily = modelLoraFamily(model);
  if (loraFamily === "unknown" || targetFamily === "unknown") return "unknown";
  return loraFamily === targetFamily ? "compatible" : "incompatible";
}

/** Mirrors the engine contract: unknown families are attempted, not rejected. */
export function loraFamilyMatches(
  baseFamily: string | null | undefined,
  model: Pick<ImageGenModelInfo, "pipeline_type" | "lora_family"> | null | undefined,
): boolean {
  return classifyLoraCompatibility(baseFamily, model) !== "incompatible";
}

/** Default manager filter: only confirmed matches; Show all reveals the rest. */
export function loraVisibleForModel(
  lora: Pick<ImageGenLoraInfo, "base_family">,
  model: Pick<ImageGenModelInfo, "pipeline_type" | "lora_family"> | null | undefined,
  showAllFamilies: boolean,
): boolean {
  return (
    showAllFamilies || classifyLoraCompatibility(lora.base_family, model) === "compatible"
  );
}

export function loraMatchesSearch(
  lora: Pick<
    ImageGenLoraInfo,
    "id" | "repo_id" | "name" | "description" | "base_family" | "source" | "license"
  >,
  query: string,
): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return [
    lora.name,
    lora.id,
    lora.repo_id,
    lora.description,
    lora.base_family,
    lora.source,
    lora.license,
  ].some((value) => value?.toLowerCase().includes(needle));
}

export interface LoraSelectionLike {
  id: string;
  scale: number;
  enabled: boolean;
}

/**
 * Preserve every selection while disabling only confirmed cross-family
 * adapters. Missing and unclassified installs remain enabled because the
 * engine intentionally attempts unknown families and reports real failures.
 */
export function disableIncompatibleLoraSelections<T extends LoraSelectionLike>(
  selections: T[],
  installed: Pick<ImageGenLoraInfo, "id" | "base_family">[],
  model: Pick<ImageGenModelInfo, "pipeline_type" | "lora_family"> | null | undefined,
): T[] {
  const installedById = new Map(installed.map((lora) => [lora.id, lora]));
  let changed = false;
  const next = selections.map((selection) => {
    const lora = installedById.get(selection.id);
    if (
      !selection.enabled ||
      !lora ||
      classifyLoraCompatibility(lora.base_family, model) !== "incompatible"
    ) {
      return selection;
    }
    changed = true;
    return { ...selection, enabled: false };
  });
  return changed ? next : selections;
}
