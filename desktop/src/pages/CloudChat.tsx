import { useSearchParams } from "react-router-dom";
import type { EngineStatus } from "@/hooks/use-engine";
import { PackageCloudChat } from "@/features/package-chat/PackageCloudChat";

interface CloudChatProps {
  engineStatus: EngineStatus;
  engineUrl: string | null;
}

/**
 * Cloud Chat is `@ai-matrx/chat` over this app's host (`features/package-chat`): history, new
 * chat, conversation room, desktop tool delegation and the Confidential pin all live there.
 * `?conversation=<id>` opens that conversation first (a coding-session row sends people here).
 * The engine is reached through the host's `localEngine`, so the props below stay only because the
 * route and panel manifest pass them.
 */
export function CloudChat(_props: CloudChatProps) {
  const [searchParams] = useSearchParams();
  return <PackageCloudChat conversationId={searchParams.get("conversation")} />;
}
