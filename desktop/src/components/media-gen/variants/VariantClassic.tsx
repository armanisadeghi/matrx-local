/**
 * VariantClassic — the original Media Generation layout: four top-level
 * tabs (Images | Video | Workflows | Library). One of the UI bake-off
 * variants selectable from the Media Generation page header.
 */

import { useState } from "react";
import {
  Film,
  Image as ImageIcon,
  Layers,
  Loader2,
  Workflow,
} from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import { ImageGenSection } from "@/components/media-gen/ImageGenSection";
import { VideoGenSection } from "@/components/media-gen/VideoGenSection";
import { WorkflowSection } from "@/components/media-gen/WorkflowSection";
import { MediaLibrarySection } from "@/components/media-gen/MediaLibrarySection";

export function VariantClassic() {
  const [state] = useMediaGenApp();
  const [tab, setTab] = useState<string>("images");

  const jobActive =
    state.activeJob?.status === "queued" ||
    state.activeJob?.status === "running";

  return (
    <Tabs
      value={tab}
      onValueChange={setTab}
      className="flex-1 flex flex-col min-h-0 px-6 pt-4"
    >
      <div className="min-w-0 shrink-0 overflow-x-auto">
        <TabsList className="w-max justify-start">
          <TabsTrigger value="images" className="gap-1.5 shrink-0">
            <ImageIcon className="h-3.5 w-3.5" />
            Images
          </TabsTrigger>
          <TabsTrigger value="video" className="gap-1.5 shrink-0">
            <Film className="h-3.5 w-3.5" />
            Video
            {jobActive && (
              <Loader2 className="h-3 w-3 animate-spin text-violet-500" />
            )}
          </TabsTrigger>
          <TabsTrigger value="workflows" className="gap-1.5 shrink-0">
            <Workflow className="h-3.5 w-3.5" />
            Workflows
          </TabsTrigger>
          <TabsTrigger value="library" className="gap-1.5 shrink-0">
            <Layers className="h-3.5 w-3.5" />
            Library
          </TabsTrigger>
        </TabsList>
      </div>

      <div className="flex-1 min-h-0 pt-6 overflow-hidden">
        <TabsContent value="images" className="m-0 h-full overflow-auto">
          <ImageGenSection />
        </TabsContent>
        <TabsContent value="video" className="m-0 h-full overflow-auto">
          <VideoGenSection />
        </TabsContent>
        <TabsContent value="workflows" className="m-0 h-full overflow-auto">
          <WorkflowSection />
        </TabsContent>
        <TabsContent value="library" className="m-0 h-full overflow-auto">
          <MediaLibrarySection />
        </TabsContent>
      </div>
    </Tabs>
  );
}
