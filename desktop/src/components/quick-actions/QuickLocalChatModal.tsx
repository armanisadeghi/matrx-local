import { useEffect, useRef } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { useLlmApp } from "@/contexts/LlmContext";
import type { EngineStatus } from "@/hooks/use-engine";

interface QuickLocalChatModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  engineStatus: EngineStatus;
  engineUrl: string | null;
  tools: string[];
}

export function QuickLocalChatModal({
  open,
  onOpenChange,
  engineStatus,
  engineUrl,
  tools,
}: QuickLocalChatModalProps) {
  const [llmState, llmActions] = useLlmApp();
  const serverRunning = llmState.serverStatus?.running ?? false;
  const hasModels = llmState.downloadedModels.length > 0;
  const autoStartedRef = useRef(false);

  useEffect(() => {
    if (
      open &&
      !serverRunning &&
      !llmState.isStarting &&
      hasModels &&
      !autoStartedRef.current
    ) {
      autoStartedRef.current = true;
      const model = llmState.downloadedModels[0];
      // Use the detected GPU layers — hardcoded 0 forced full CPU inference
      // for quick-launched chats (drastically slower than the Models page).
      const gpuLayers = llmState.hardwareResult?.recommended_gpu_layers ?? 0;
      llmActions.startServer(model.filename, gpuLayers).catch((e) => console.warn("[local-chat] startServer failed:", e));
    }
    if (!open) {
      autoStartedRef.current = false;
    }
  }, [
    open,
    serverRunning,
    llmState.isStarting,
    hasModels,
    llmState.downloadedModels,
    llmActions,
  ]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[80vh] max-h-[80vh] max-w-2xl flex-col gap-0 p-0">
        <DialogHeader className="shrink-0 px-6 pt-6 pb-2">
          <DialogTitle>Quick Confidential Chat</DialogTitle>
          {!serverRunning && !llmState.isStarting && !hasModels && (
            <DialogDescription className="text-amber-500">
              No on-device model is set up yet. Open Confidential Chat to add a
              model and get started.
            </DialogDescription>
          )}
          {llmState.isStarting && (
            <DialogDescription className="text-sky-500">
              Starting confidential chat…
            </DialogDescription>
          )}
        </DialogHeader>
        <div className="min-h-0 flex-1 overflow-hidden">
          <ChatPanel
            engineStatus={engineStatus}
            engineUrl={engineUrl}
            tools={tools}
            compact
            forceLocalModel
          />
        </div>
      </DialogContent>
    </Dialog>
  );
}
