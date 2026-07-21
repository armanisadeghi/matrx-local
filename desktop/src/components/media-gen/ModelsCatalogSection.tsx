/**
 * ModelsCatalogSection — full grid catalog for image + video models (Classic Models tab).
 */

import { Film, Image as ImageIcon } from "lucide-react";
import { useImageGenController } from "./core/imageController";
import { useVideoGenController } from "./core/videoController";
import { ImageGenGate, VideoGenGate } from "./core/gates";
import { ImageModelPicker, VideoModelPicker } from "./core/ModelPicker";

export function ModelsCatalogSection() {
  const imageCtl = useImageGenController();
  const videoCtl = useVideoGenController();

  return (
    <div className="space-y-6 pb-4">
      <section className="space-y-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <ImageIcon className="h-4 w-4 text-violet-500" />
          Image models
        </h3>
        <ImageGenGate>
          <ImageModelPicker ctl={imageCtl} layout="grid" showHeading={false} />
        </ImageGenGate>
      </section>

      <section className="space-y-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <Film className="h-4 w-4 text-violet-500" />
          Video models
        </h3>
        <VideoGenGate showRuntimePanel={false}>
          <VideoModelPicker ctl={videoCtl} layout="grid" showHeading={false} />
        </VideoGenGate>
      </section>
    </div>
  );
}
