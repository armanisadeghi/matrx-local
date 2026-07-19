/**
 * ImageGenSection — the "Images" experience of the media-gen tab.
 *
 * THIN composition of the canonical core/ pieces: Generate|Models sub-tabs,
 * the ImageGenerateForm (all controls incl. img2img + LoRA), the result pane
 * and the shared queue panel. All logic lives in core/imageController and
 * MediaGenContext — this file is layout only.
 */

import { Image as ImageIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import { SubTabBar } from "./shared";
import { useImageGenController } from "./core/imageController";
import { ImageGenGate } from "./core/gates";
import { ImageModelPicker } from "./core/ModelPicker";
import {
  ImageGenerateForm,
  ImageParamsLoading,
} from "./core/ImageGenerateForm";
import { ImageResultPane } from "./core/ResultView";
import { ImageQueuePanel } from "./core/ImageQueuePanel";

export function ImageGenSection() {
  const [state, actions] = useMediaGenApp();
  const { imageForm } = state;
  const { setImageForm } = actions;
  const ctl = useImageGenController();

  return (
    <ImageGenGate>
      <div className="space-y-5 pb-8">
          <SubTabBar
            tabs={[
              {
                id: "generate" as const,
                label: "Generate",
                badge: ctl.activeJobCount,
              },
              { id: "models" as const, label: "Models" },
            ]}
            active={imageForm.view}
            onSelect={(view) => setImageForm({ view })}
          />

          {imageForm.view === "models" ? (
            <ImageModelPicker ctl={ctl} layout="grid" />
          ) : !ctl.model ? (
            <div className="rounded-xl border border-dashed px-5 py-10 flex flex-col items-center text-center gap-3">
              <ImageIcon className="h-8 w-8 text-muted-foreground/40" />
              <p className="text-sm font-medium">No model selected</p>
              <p className="text-xs text-muted-foreground max-w-sm">
                Pick a model in the Models tab — its full settings will appear
                here.
              </p>
              <Button
                size="sm"
                onClick={() => setImageForm({ view: "models" })}
              >
                Choose a model
              </Button>
            </div>
          ) : imageForm.paramsLoading || !ctl.defaults ? (
            <ImageParamsLoading ctl={ctl} />
          ) : (
            <div className="space-y-5">
              <div className="grid gap-6 lg:grid-cols-2">
                <ImageGenerateForm ctl={ctl} showHeader />
                <div className="space-y-3">
                  <ImageResultPane />
                </div>
              </div>
              <ImageQueuePanel layout="list" />
            </div>
          )}
      </div>
    </ImageGenGate>
  );
}
