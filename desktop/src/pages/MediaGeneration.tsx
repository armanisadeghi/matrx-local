/**
 * MediaGeneration — top-level page for on-device image & video generation.
 *
 * Replaces the old "Image & Video" tab that lived inside the Confidential
 * Chat page (LocalModels.tsx). All persistent media-gen state lives in
 * MediaGenContext (App-level provider), so running jobs, queues, form
 * state and results survive navigation — including when switching layout.
 *
 * UI BAKE-OFF: five selectable layout variants (header dropdown, persisted
 * to localStorage). All variants share the same context state — switching
 * layouts never loses in-flight work. Once a winner is picked, the losers
 * and this switcher get deleted (no dead code left behind).
 */

import { useState } from "react";
import { LayoutTemplate, Loader2 } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import { VariantClassic } from "@/components/media-gen/variants/VariantClassic";
import { VariantStudio } from "@/components/media-gen/variants/VariantStudio";
import { VariantWorkspace } from "@/components/media-gen/variants/VariantWorkspace";
import { VariantGallery } from "@/components/media-gen/variants/VariantGallery";
import { VariantFocus } from "@/components/media-gen/variants/VariantFocus";

const VARIANTS = [
  { id: "classic", label: "Classic tabs", Component: VariantClassic },
  { id: "studio", label: "Studio split-pane", Component: VariantStudio },
  { id: "workspace", label: "Workspace nav", Component: VariantWorkspace },
  { id: "gallery", label: "Gallery first", Component: VariantGallery },
  { id: "focus", label: "Focus flow", Component: VariantFocus },
] as const;

type VariantId = (typeof VARIANTS)[number]["id"];

const VARIANT_KEY = "media-gen-ui-variant";

function readVariant(): VariantId {
  try {
    const raw = localStorage.getItem(VARIANT_KEY);
    if (raw && VARIANTS.some((v) => v.id === raw)) return raw as VariantId;
  } catch {
    // fall through to default
  }
  return "studio";
}

export function MediaGeneration() {
  const [state] = useMediaGenApp();
  const [variantId, setVariantId] = useState<VariantId>(readVariant);

  const selectVariant = (id: string) => {
    const next = VARIANTS.some((v) => v.id === id)
      ? (id as VariantId)
      : "classic";
    setVariantId(next);
    try {
      localStorage.setItem(VARIANT_KEY, next);
    } catch {
      // persistence is best-effort
    }
  };

  const jobActive =
    state.activeJob?.status === "queued" ||
    state.activeJob?.status === "running";

  const Active =
    VARIANTS.find((v) => v.id === variantId)?.Component ?? VariantClassic;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PageHeader
        title="Media Generation"
        description="On-device AI image and video generation"
      >
        <div className="flex items-center gap-2">
          {jobActive && (
            <Badge className="bg-violet-500/20 text-violet-600 dark:text-violet-400 border-violet-500/30 gap-1.5">
              <Loader2 className="h-3 w-3 animate-spin" />
              Video job running
            </Badge>
          )}
          <Select value={variantId} onValueChange={selectVariant}>
            <SelectTrigger className="h-8 w-[190px] gap-1.5 text-xs">
              <LayoutTemplate className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <SelectValue placeholder="Layout" />
            </SelectTrigger>
            <SelectContent>
              {VARIANTS.map((v) => (
                <SelectItem key={v.id} value={v.id} className="text-xs">
                  {v.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </PageHeader>

      <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
        <Active />
      </div>
    </div>
  );
}
