/**
 * VideoGenSection — the "Video" experience of the Classic media-gen tab.
 *
 * Generate-only layout: compact model bar + form. Full model grids live on the
 * Models top-level tab (ModelsCatalogSection).
 */

import { useMediaGenApp } from "@/contexts/MediaGenContext";
import { useVideoGenController } from "./core/videoController";
import { VideoGenGate } from "./core/gates";
import { VideoModelBar } from "./core/ModelPicker";
import {
  VideoGenerateForm,
  VideoParamsLoading,
} from "./core/VideoGenerateForm";
import {
  ActiveVideoJobCard,
  VideoJobsList,
  VideoPlayback,
} from "./core/VideoJobPanel";

export function VideoGenSection() {
  const [state] = useMediaGenApp();
  const { videoForm } = state;
  const ctl = useVideoGenController();

  return (
    <VideoGenGate>
      <div className="space-y-3 pb-4">
        <VideoModelBar ctl={ctl} />

        <ActiveVideoJobCard />
        <VideoPlayback ctl={ctl} />

        {!ctl.model ? (
          <p className="px-1 text-xs text-muted-foreground">
            Choose a model above to configure video generation settings.
          </p>
        ) : videoForm.paramsLoading || !ctl.defaults ? (
          <VideoParamsLoading ctl={ctl} />
        ) : (
          <div className="max-w-xl">
            <VideoGenerateForm ctl={ctl} />
          </div>
        )}

        <VideoJobsList ctl={ctl} layout="list" />
      </div>
    </VideoGenGate>
  );
}
