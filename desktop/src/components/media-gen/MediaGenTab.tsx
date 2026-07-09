/**
 * MediaGenTab — the thin entry point mounted by LocalModels.tsx for the
 * "Image & Video" tab.  Splits the experience into two first-class sub-tabs:
 * Images and Video.  All heavy lifting lives in ImageGenSection /
 * VideoGenSection and in the shared MediaGenContext (App-level provider).
 */

import { useState } from "react";
import { Image as ImageIcon, Film, Loader2 } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import { ImageGenSection } from "./ImageGenSection";
import { VideoGenSection } from "./VideoGenSection";

export function MediaGenTab() {
  const [state] = useMediaGenApp();
  const [subTab, setSubTab] = useState<string>("images");

  const jobActive =
    state.activeJob?.status === "queued" ||
    state.activeJob?.status === "running";

  return (
    <Tabs value={subTab} onValueChange={setSubTab} className="space-y-4">
      <TabsList className="w-fit">
        <TabsTrigger value="images" className="gap-1.5">
          <ImageIcon className="h-3.5 w-3.5" />
          Images
        </TabsTrigger>
        <TabsTrigger value="video" className="gap-1.5">
          <Film className="h-3.5 w-3.5" />
          Video
          {jobActive && (
            <Loader2 className="h-3 w-3 animate-spin text-violet-500" />
          )}
        </TabsTrigger>
      </TabsList>

      <TabsContent value="images" className="m-0">
        <ImageGenSection />
      </TabsContent>
      <TabsContent value="video" className="m-0">
        <VideoGenSection />
      </TabsContent>
    </Tabs>
  );
}
