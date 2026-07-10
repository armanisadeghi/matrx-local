/**
 * VideoGenSection — the "Video" experience of the media-gen tab.
 *
 * THIN composition of the canonical core/ pieces: Generate|Models sub-tabs,
 * the always-visible active-job card, playback, the VideoGenerateForm and the
 * shared recent-jobs list. All logic lives in core/videoController and
 * MediaGenContext — this file is layout only.
 */

import { Film } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import { SubTabBar } from "./shared";
import { useVideoGenController } from "./core/videoController";
import { VideoGenGate } from "./core/gates";
import { VideoModelPicker } from "./core/ModelPicker";
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
  const [state, actions] = useMediaGenApp();
  const { videoForm } = state;
  const { setVideoForm } = actions;
  const ctl = useVideoGenController();

  return (
    <VideoGenGate>
      <div className="space-y-5 pb-8">
        <SubTabBar
          tabs={[
            {
              id: "generate" as const,
              label: "Generate",
              badge: ctl.jobIsActive ? 1 : null,
            },
            { id: "models" as const, label: "Models" },
          ]}
          active={videoForm.view}
          onSelect={(view) => setVideoForm({ view })}
        />

        {/* Active job progress + playback (always visible, both sub-tabs) */}
        <ActiveVideoJobCard />
        <VideoPlayback ctl={ctl} />

        {videoForm.view === "models" ? (
          <VideoModelPicker ctl={ctl} layout="grid" />
        ) : !ctl.model ? (
          <div className="rounded-xl border border-dashed px-5 py-10 flex flex-col items-center text-center gap-3">
            <Film className="h-8 w-8 text-muted-foreground/40" />
            <p className="text-sm font-medium">No model selected</p>
            <p className="text-xs text-muted-foreground max-w-sm">
              Pick a model in the Models tab — its full settings will appear
              here.
            </p>
            <Button size="sm" onClick={() => setVideoForm({ view: "models" })}>
              Choose a model
            </Button>
          </div>
        ) : videoForm.paramsLoading || !ctl.defaults ? (
          <VideoParamsLoading ctl={ctl} />
        ) : (
          <div className="max-w-xl">
            <VideoGenerateForm ctl={ctl} showHeader />
          </div>
        )}

        <VideoJobsList ctl={ctl} layout="list" />
      </div>
    </VideoGenGate>
  );
}
