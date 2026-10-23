/**
 * Cloud Chat on `@ai-matrx/chat` (L1, the only Cloud Chat: `/cloud-chat`): the package's history,
 * new-chat screen and conversation room over Matrx Local's host (`./host`). The desktop has no
 * Cloud Chat code of its own.
 */

import { ChatConversationRoom } from "@ai-matrx/chat/agents/components/chat/ChatConversationRoom";
import { ChatNewClient } from "@ai-matrx/chat/agents/components/chat/ChatNewClient";
import { ChatRoomClient } from "@ai-matrx/chat/agents/components/chat/ChatRoomClient";
import type { ChatHost } from "@ai-matrx/chat/host";
import { ConversationSidebar } from "@ai-matrx/chat/sidebar/ConversationSidebar";
import { entryWebUrl, type SidebarEntry } from "@ai-matrx/chat/sidebar/sidebar-buckets";
import { ChatProvider, usePathname, useRouter } from "@ai-matrx/chat/host/react";
import { Button } from "@ai-matrx/design-system";
import { PanelLeft, SquarePen } from "lucide-react";
import { useEffect, useState } from "react";
import { CodingReplyNotice } from "@ai-matrx/chat/agents/coding-session-reply/CodingReplyNotice";
import { useCodingReplyResponder } from "@ai-matrx/chat/agents/coding-session-reply/useCodingReplyResponder";
import { useChatServerResponderRead } from "@ai-matrx/chat/agents/coding-session-reply/useChatServerResponderRead";
import { registerDesktopToolRenderers } from "./desktop-tool-renderers";
import { createDesktopChatHost } from "./host";

registerDesktopToolRenderers();

/** Who answers a reply on a mirrored coding session — the package's one notice, over the chat server port. */
function ReplyNotice({ conversationId }: { conversationId: string }) {
  const read = useChatServerResponderRead();
  const state = useCodingReplyResponder({ conversationId, enabled: true, read });
  return <CodingReplyNotice state={state} className="shrink-0 px-4 pt-1" />;
}

/** The sidebar's entries have no page in this app: they open on the web (the package's one entry map), in the browser. */
function openEntryOnWeb(entry: SidebarEntry): boolean {
  const url = entryWebUrl(entry, "https://www.aimatrx.com");
  if (!url) return false; // Favorites: the sidebar answers it itself (its Pinned section).
  void window.open(url, "_blank");
  return true;
}

function Screens() {
  const pathname = usePathname();
  const router = useRouter();
  const [historyOpen, setHistoryOpen] = useState(true);
  const conversationId = pathname.match(/^\/chat\/([0-9a-f-]{36})/)?.[1] ?? null;
  // `/chat/a/<agent>`: the package's agent picker routes here; the agent answers a fresh chat directly.
  const directAgentId = pathname.match(/^\/chat\/a\/([^/?#]+)/)?.[1] ?? null;
  return (
    <div data-package-chat="" className="flex h-full min-h-0 overflow-hidden">
      {historyOpen && (
        <div className="flex w-72 shrink-0 flex-col border-r border-border/60">
          <ConversationSidebar
            activeConversationId={conversationId}
            onOpenConversation={(conversation) => router.push(`/chat/${conversation.id}`)}
            onNew={() => router.push("/chat")}
            onEntry={openEntryOnWeb}
            className="min-h-0 flex-1"
          />
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex shrink-0 items-center gap-1 px-2 py-1">
          <Button
            variant="ghost"
            size="sm"
            aria-label="Conversations"
            aria-pressed={historyOpen}
            onClick={() => setHistoryOpen((open) => !open)}
          >
            <PanelLeft className="size-4" />
          </Button>
          <Button variant="ghost" size="sm" aria-label="New chat" onClick={() => router.push("/chat")}>
            <SquarePen className="size-4" />
          </Button>
        </div>
        {conversationId ? <ReplyNotice key={conversationId} conversationId={conversationId} /> : null}
        <div className="min-h-0 flex-1">
          {conversationId ? (
            <ChatConversationRoom
              key={conversationId}
              conversationId={conversationId}
              agentId={null}
              ownedByMandate={false}
            />
          ) : directAgentId ? (
            <ChatRoomClient
              key={directAgentId}
              agentId={decodeURIComponent(directAgentId)}
              composer={{ initialMode: null }}
            />
          ) : (
            <ChatNewClient agentId={null} composer={{ initialMode: null }} />
          )}
        </div>
      </div>
    </div>
  );
}

export function PackageCloudChat({ conversationId }: { conversationId?: string | null }) {
  const [host, setHost] = useState<ChatHost | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    createDesktopChatHost({ conversationId }).then(
      (h) => live && setHost(h),
      (e: unknown) => live && setError(e instanceof Error ? e.message : String(e)),
    );
    return () => {
      live = false;
    };
  }, [conversationId]);
  if (error) return <p className="p-3 text-sm text-destructive">Chat could not start: {error}</p>;
  if (!host) return null;
  return (
    <ChatProvider host={host}>
      <Screens />
    </ChatProvider>
  );
}
