/**
 * VariantClassic — compact top-level tabs: Images | Video | … | Models | Library.
 * Generate tabs use a one-row model bar; full model grids live on Models.
 */

import { useState } from "react";
import {
  Boxes,
  Film,
  Image as ImageIcon,
  Layers,
  ListTree,
  Loader2,
  MessageSquareText,
  Shuffle,
  Workflow,
} from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import { ImageGenSection } from "@/components/media-gen/ImageGenSection";
import { VideoGenSection } from "@/components/media-gen/VideoGenSection";
import { WorkflowSection } from "@/components/media-gen/WorkflowSection";
import { MediaLibrarySection } from "@/components/media-gen/MediaLibrarySection";
import { ListLibrarySection } from "@/components/media-gen/ListLibrarySection";
import { SavedPromptsSection } from "@/components/media-gen/SavedPromptsSection";
import { VariationBatchesSection } from "@/components/media-gen/VariationBatchesSection";
import { ModelsCatalogSection } from "@/components/media-gen/ModelsCatalogSection";

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
      className="flex min-h-0 flex-1 flex-col px-4 pt-1"
    >
      <div className="min-w-0 shrink-0 overflow-x-auto">
        <TabsList className="h-9 w-max justify-start">
          <TabsTrigger value="images" className="h-8 gap-1.5 shrink-0 text-xs">
            <ImageIcon className="h-3.5 w-3.5" />
            Images
          </TabsTrigger>
          <TabsTrigger value="video" className="h-8 gap-1.5 shrink-0 text-xs">
            <Film className="h-3.5 w-3.5" />
            Video
            {jobActive && (
              <Loader2 className="h-3 w-3 animate-spin text-violet-500" />
            )}
          </TabsTrigger>
          <TabsTrigger value="models" className="h-8 gap-1.5 shrink-0 text-xs">
            <Boxes className="h-3.5 w-3.5" />
            Models
          </TabsTrigger>
          <TabsTrigger
            value="workflows"
            className="h-8 gap-1.5 shrink-0 text-xs"
          >
            <Workflow className="h-3.5 w-3.5" />
            Workflows
          </TabsTrigger>
          <TabsTrigger value="lists" className="h-8 gap-1.5 shrink-0 text-xs">
            <ListTree className="h-3.5 w-3.5" />
            Lists
          </TabsTrigger>
          <TabsTrigger value="prompts" className="h-8 gap-1.5 shrink-0 text-xs">
            <MessageSquareText className="h-3.5 w-3.5" />
            Prompts
          </TabsTrigger>
          <TabsTrigger
            value="variations"
            className="h-8 gap-1.5 shrink-0 text-xs"
          >
            <Shuffle className="h-3.5 w-3.5" />
            Variations
          </TabsTrigger>
          <TabsTrigger value="library" className="h-8 gap-1.5 shrink-0 text-xs">
            <Layers className="h-3.5 w-3.5" />
            Library
          </TabsTrigger>
        </TabsList>
      </div>

      <div className="min-h-0 flex-1 overflow-auto pt-2">
        <TabsContent value="images" className="m-0">
          <ImageGenSection />
        </TabsContent>
        <TabsContent value="video" className="m-0">
          <VideoGenSection />
        </TabsContent>
        <TabsContent value="models" className="m-0 h-full overflow-auto">
          <ModelsCatalogSection />
        </TabsContent>
        <TabsContent value="workflows" className="m-0 h-full overflow-auto">
          <WorkflowSection />
        </TabsContent>
        <TabsContent value="lists" className="m-0 h-full overflow-auto">
          <ListLibrarySection />
        </TabsContent>
        <TabsContent value="prompts" className="m-0 h-full overflow-hidden">
          <SavedPromptsSection />
        </TabsContent>
        <TabsContent value="variations" className="m-0 h-full overflow-hidden">
          <VariationBatchesSection />
        </TabsContent>
        <TabsContent value="library" className="m-0 h-full overflow-auto">
          <MediaLibrarySection />
        </TabsContent>
      </div>
    </Tabs>
  );
}
