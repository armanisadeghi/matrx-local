/**
 * ImageGenSection — the "Images" experience of the Classic media-gen tab.
 *
 * Generate-only layout: compact model bar + form + result. Full model grids
 * live on the Models top-level tab (ModelsCatalogSection).
 */

import { useMediaGenApp } from "@/contexts/MediaGenContext";
import { useImageGenController } from "./core/imageController";
import { ImageGenGate } from "./core/gates";
import { ImageModelBar } from "./core/ModelPicker";
import {
  ImageGenerateForm,
  ImageParamsLoading,
} from "./core/ImageGenerateForm";
import { ImageResultPane } from "./core/ResultView";
import { ImageQueuePanel } from "./core/ImageQueuePanel";

export function ImageGenSection() {
  const [state] = useMediaGenApp();
  const { imageForm } = state;
  const ctl = useImageGenController();

  return (
    <ImageGenGate>
      <div className="space-y-3 pb-4">
        <ImageModelBar ctl={ctl} />

        {!ctl.model ? (
          <p className="px-1 text-xs text-muted-foreground">
            Choose a model above to configure generation settings.
          </p>
        ) : imageForm.paramsLoading || !ctl.defaults ? (
          <ImageParamsLoading ctl={ctl} />
        ) : (
          <>
            <div className="grid gap-4 lg:grid-cols-2">
              <ImageGenerateForm ctl={ctl} />
              <ImageResultPane />
            </div>
            <ImageQueuePanel layout="split" showHeading={false} />
          </>
        )}
      </div>
    </ImageGenGate>
  );
}
