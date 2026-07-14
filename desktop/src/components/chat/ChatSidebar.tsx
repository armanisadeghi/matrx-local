import { type ReactNode, useState } from "react";
import {
  Plus,
  Search,
  MessageSquare,
  Trash2,
  PanelLeftClose,
  PanelLeft,
  Pencil,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { Conversation } from "@/hooks/use-chat";

interface ChatSidebarProps {
  conversations: Conversation[];
  groupedConversations: Record<string, Conversation[]>;
  activeConversationId: string | null;
  collapsed: boolean;
  onToggle: () => void;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onRename: (id: string, title: string) => void;
  headerContent?: ReactNode;
}

export function ChatSidebar({
  groupedConversations,
  activeConversationId,
  collapsed,
  onToggle,
  onSelect,
  onNew,
  onDelete,
  onRename,
  headerContent,
}: ChatSidebarProps) {
  const [search, setSearch] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [hoveredId, setHoveredId] = useState<string | null>(null);

  const dateGroups = [
    "Today",
    "Yesterday",
    "Previous 7 days",
    "Previous 30 days",
    "Older",
  ];

  const filteredGroups = Object.fromEntries(
    Object.entries(groupedConversations).map(([group, convs]) => [
      group,
      convs.filter((c) =>
        c.title.toLowerCase().includes(search.toLowerCase())
      ),
    ])
  );

  const startRename = (id: string, currentTitle: string) => {
    setEditingId(id);
    setEditTitle(currentTitle);
  };

  const commitRename = () => {
    if (editingId && editTitle.trim()) {
      onRename(editingId, editTitle.trim());
    }
    setEditingId(null);
  };

  if (collapsed) {
    return (
      <div className="flex h-full w-12 flex-col items-center border-r bg-sidebar py-3">
        <Tooltip delayDuration={0}>
          <TooltipTrigger asChild>
            <button
              className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-foreground"
              onClick={onToggle}
            >
              <PanelLeft className="h-4 w-4" />
            </button>
          </TooltipTrigger>
          <TooltipContent side="right">Open sidebar</TooltipContent>
        </Tooltip>

        <div className="mt-3">
          <Tooltip delayDuration={0}>
            <TooltipTrigger asChild>
              <button
                className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-foreground"
                onClick={onNew}
              >
                <Plus className="h-4 w-4" />
              </button>
            </TooltipTrigger>
            <TooltipContent side="right">New chat</TooltipContent>
          </Tooltip>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full w-64 flex-col border-r bg-sidebar">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-3">
        <button
          type="button"
          className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-foreground"
          onClick={onToggle}
        >
          <PanelLeftClose className="h-4 w-4" />
        </button>
        <div className="min-w-0 flex-1">{headerContent}</div>
        <Tooltip delayDuration={0}>
          <TooltipTrigger asChild>
            <button
              type="button"
              className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-foreground"
              onClick={onNew}
            >
              <Plus className="h-4 w-4" />
            </button>
          </TooltipTrigger>
          <TooltipContent side="bottom">New chat</TooltipContent>
        </Tooltip>
      </div>

      {/* Search */}
      <div className="px-3 pb-2">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            placeholder="Search chats..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="h-8 w-full rounded-md border-0 bg-sidebar-accent pl-8 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
          />
        </div>
      </div>

      {/* Conversation List */}
      <ScrollArea className="flex-1 px-1.5">
        {dateGroups.map((group) => {
          const convs = filteredGroups[group];
          if (!convs?.length) return null;

          return (
            <div key={group} className="mb-1">
              <div className="px-2 py-1.5">
                <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                  {group}
                </span>
              </div>
              {convs.map((conv) => {
                const isActive = activeConversationId === conv.id;
                return (
                  <div
                    key={conv.id}
                    className={cn(
                      "group relative mx-1 mb-0.5 flex items-center rounded-md px-2.5 py-2 cursor-pointer transition-colors",
                      isActive
                        ? "bg-sidebar-accent text-sidebar-foreground font-medium"
                        : "text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground"
                    )}
                    onClick={() => onSelect(conv.id)}
                    onMouseEnter={() => setHoveredId(conv.id)}
                    onMouseLeave={() => setHoveredId(null)}
                  >
                    <MessageSquare className="mr-2 h-3.5 w-3.5 shrink-0 opacity-40" />
                    {editingId === conv.id ? (
                      <input
                        className="flex-1 bg-transparent text-xs outline-none"
                        value={editTitle}
                        onChange={(e) => setEditTitle(e.target.value)}
                        onBlur={commitRename}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") commitRename();
                          if (e.key === "Escape") setEditingId(null);
                        }}
                        autoFocus
                        onClick={(e) => e.stopPropagation()}
                      />
                    ) : (
                      <span className="flex-1 truncate text-xs">
                        {conv.title}
                      </span>
                    )}

                    {/* Action buttons on hover */}
                    {hoveredId === conv.id && editingId !== conv.id && (
                      <div className="absolute right-1 flex items-center gap-0.5">
                        <button
                          className="rounded p-1 text-muted-foreground transition-colors hover:text-foreground"
                          onClick={(e) => {
                            e.stopPropagation();
                            startRename(conv.id, conv.title);
                          }}
                        >
                          <Pencil className="h-3 w-3" />
                        </button>
                        <button
                          className="rounded p-1 text-muted-foreground transition-colors hover:text-red-500"
                          onClick={(e) => {
                            e.stopPropagation();
                            onDelete(conv.id);
                          }}
                        >
                          <Trash2 className="h-3 w-3" />
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          );
        })}

        {Object.values(filteredGroups).every((g) => !g?.length) && (
          <div className="flex flex-col items-center justify-center py-8">
            <MessageSquare className="mb-2 h-8 w-8 text-muted-foreground/30" />
            <p className="text-xs text-muted-foreground">
              {search ? "No matching chats" : "No conversations yet"}
            </p>
          </div>
        )}
      </ScrollArea>
    </div>
  );
}
