"use client";

/**
 * PromptPicker
 *
 * A reusable component for selecting, creating, editing, and managing
 * system prompts from the system-prompts library.
 *
 * Usage (simple inline picker):
 *   <PromptPicker onSelect={(content) => setSystemPrompt(content)} />
 *
 * Usage (with management dialog):
 *   <PromptPicker showManage onSelect={(content) => setSystemPrompt(content)} />
 */

import { useState } from "react";
import { systemPrompts, BUILTIN_PROMPTS } from "@/lib/system-prompts";
import type { SystemPrompt } from "@/lib/system-prompts";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  BookOpen,
  Plus,
  Pencil,
  Trash2,
  Copy,
  Check,
  Search,
  ChevronRight,
} from "lucide-react";

// ── Types ──────────────────────────────────────────────────────────────────

interface PromptPickerProps {
  /** Called when a prompt is selected — receives the prompt content string. */
  onSelect: (content: string, name: string) => void;
  /** Current system prompt content (to highlight which prompt is active). */
  currentContent?: string;
  /** Label for the trigger button. Defaults to "System Prompt". */
  triggerLabel?: string;
  className?: string;
}

// ── Inline list item ───────────────────────────────────────────────────────

function PromptListItem({
  name,
  category,
  content,
  isBuiltin,
  isActive,
  onSelect,
  onEdit,
  onDelete,
  onFork,
}: {
  name: string;
  category: string;
  content: string;
  isBuiltin?: boolean;
  isActive?: boolean;
  onSelect: () => void;
  onEdit?: () => void;
  onDelete?: () => void;
  onFork?: () => void;
}) {
  const [copied, setCopied] = useState(false);

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div
      className={`group flex items-start gap-3 rounded-lg px-3 py-2.5 cursor-pointer transition-colors ${
        isActive
          ? "bg-primary/10 border border-primary/20"
          : "hover:bg-muted/60 border border-transparent"
      }`}
      onClick={onSelect}
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <span className="text-sm font-medium truncate">{name}</span>
          <Badge variant="outline" className="text-[10px] px-1.5 py-0 shrink-0">
            {category}
          </Badge>
          {isBuiltin && (
            <Badge variant="secondary" className="text-[10px] px-1.5 py-0 shrink-0">
              built-in
            </Badge>
          )}
          {isActive && <Check className="h-3.5 w-3.5 text-primary shrink-0" />}
        </div>
        <p className="text-xs text-muted-foreground line-clamp-2">{content}</p>
      </div>
      <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
        <button
          className="p-1 rounded hover:bg-muted/80 text-muted-foreground hover:text-foreground"
          onClick={handleCopy}
          title="Copy content"
        >
          {copied ? <Check className="h-3 w-3 text-green-500" /> : <Copy className="h-3 w-3" />}
        </button>
        {isBuiltin ? (
          <button
            className="p-1 rounded hover:bg-muted/80 text-muted-foreground hover:text-foreground"
            onClick={(e) => { e.stopPropagation(); onFork?.(); }}
            title="Save a copy to your library"
          >
            <Plus className="h-3 w-3" />
          </button>
        ) : (
          <>
            <button
              className="p-1 rounded hover:bg-muted/80 text-muted-foreground hover:text-foreground"
              onClick={(e) => { e.stopPropagation(); onEdit?.(); }}
              title="Edit"
            >
              <Pencil className="h-3 w-3" />
            </button>
            <button
              className="p-1 rounded hover:bg-muted/80 text-muted-foreground hover:text-destructive"
              onClick={(e) => { e.stopPropagation(); onDelete?.(); }}
              title="Delete"
            >
              <Trash2 className="h-3 w-3" />
            </button>
          </>
        )}
        <ChevronRight className="h-3 w-3 text-muted-foreground/50" />
      </div>
    </div>
  );
}

// ── Prompt editor form ─────────────────────────────────────────────────────

function PromptEditor({
  initial,
  onSave,
  onCancel,
}: {
  initial?: Partial<SystemPrompt>;
  onSave: (data: { name: string; content: string; category: string }) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [content, setContent] = useState(initial?.content ?? "");
  const [category, setCategory] = useState(initial?.category ?? "General");
  const categories = systemPrompts.categories();

  const canSave = name.trim().length > 0 && content.trim().length > 0;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label className="text-xs">Name</Label>
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Technical Writer"
            autoFocus
          />
        </div>
        <div className="space-y-1.5">
          <Label className="text-xs">Category</Label>
          <Input
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            placeholder="e.g. Writing"
            list="prompt-categories"
          />
          <datalist id="prompt-categories">
            {categories.map((c) => <option key={c} value={c} />)}
          </datalist>
        </div>
      </div>
      <div className="space-y-1.5">
        <Label className="text-xs">System Prompt</Label>
        <Textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder="You are a helpful assistant that…"
          className="resize-none h-36 text-sm"
        />
        <p className="text-xs text-muted-foreground">
          {content.length} characters
        </p>
      </div>
      <div className="flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
        <Button size="sm" disabled={!canSave} onClick={() => onSave({ name, content, category })}>
          Save Prompt
        </Button>
      </div>
    </div>
  );
}

// ── Management dialog ──────────────────────────────────────────────────────

function PromptManageDialog({
  open,
  onOpenChange,
  onSelect,
  currentContent,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onSelect: (content: string, name: string) => void;
  currentContent?: string;
}) {
  const [userPrompts, setUserPrompts] = useState<SystemPrompt[]>(() => systemPrompts.list());
  const [editing, setEditing] = useState<SystemPrompt | null>(null);
  const [creating, setCreating] = useState(false);
  const [search, setSearch] = useState("");
  const [filterCategory, setFilterCategory] = useState<string | null>(null);

  const reload = () => setUserPrompts(systemPrompts.list());

  const handleCreate = (data: { name: string; content: string; category: string }) => {
    systemPrompts.create(data);
    reload();
    setCreating(false);
  };

  const handleUpdate = (data: { name: string; content: string; category: string }) => {
    if (!editing) return;
    systemPrompts.update(editing.id, data);
    reload();
    setEditing(null);
  };

  const handleDelete = (id: string) => {
    systemPrompts.delete(id);
    reload();
  };

  const handleFork = (builtinId: string) => {
    systemPrompts.forkBuiltin(builtinId);
    reload();
  };

  const categories = systemPrompts.categories();

  const filterPrompts = <T extends { name: string; content: string; category: string }>(list: T[]): T[] => {
    return list.filter((p) => {
      const matchesSearch =
        !search ||
        p.name.toLowerCase().includes(search.toLowerCase()) ||
        p.content.toLowerCase().includes(search.toLowerCase());
      const matchesCategory = !filterCategory || p.category === filterCategory;
      return matchesSearch && matchesCategory;
    });
  };

  const filteredBuiltins = filterPrompts(BUILTIN_PROMPTS);
  const filteredUser = filterPrompts(userPrompts);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh] flex flex-col p-0">
        <DialogHeader className="px-5 py-4 border-b shrink-0">
          <DialogTitle className="flex items-center gap-2">
            <BookOpen className="h-4 w-4" />
            System Prompt Library
          </DialogTitle>
        </DialogHeader>

        {/* Search + filter bar */}
        <div className="flex items-center gap-2 px-4 py-3 border-b shrink-0">
          <div className="relative flex-1">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search prompts…"
              className="pl-8 h-8 text-sm"
            />
          </div>
          <div className="flex gap-1 flex-wrap">
            <button
              className={`text-xs px-2 py-1 rounded-md border transition-colors ${
                !filterCategory ? "bg-primary/10 border-primary/30 text-primary" : "border-transparent hover:bg-muted"
              }`}
              onClick={() => setFilterCategory(null)}
            >
              All
            </button>
            {categories.map((cat) => (
              <button
                key={cat}
                className={`text-xs px-2 py-1 rounded-md border transition-colors ${
                  filterCategory === cat ? "bg-primary/10 border-primary/30 text-primary" : "border-transparent hover:bg-muted"
                }`}
                onClick={() => setFilterCategory(filterCategory === cat ? null : cat)}
              >
                {cat}
              </button>
            ))}
          </div>
          <Button size="sm" className="shrink-0 gap-1 h-8" onClick={() => { setCreating(true); setEditing(null); }}>
            <Plus className="h-3.5 w-3.5" />
            New
          </Button>
        </div>

        <ScrollArea className="flex-1 px-4 py-3">
          {/* Create / edit form */}
          {(creating || editing) && (
            <div className="mb-4 rounded-lg border bg-muted/20 p-4">
              <p className="text-xs font-semibold text-muted-foreground mb-3 uppercase tracking-wide">
                {editing ? "Edit Prompt" : "Create New Prompt"}
              </p>
              <PromptEditor
                key={editing?.id ?? "new"}
                initial={editing ?? undefined}
                onSave={editing ? handleUpdate : handleCreate}
                onCancel={() => { setCreating(false); setEditing(null); }}
              />
            </div>
          )}

          {/* User prompts */}
          {filteredUser.length > 0 && (
            <div className="mb-4">
              <p className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wide mb-2 px-1">
                My Prompts ({filteredUser.length})
              </p>
              <div className="space-y-0.5">
                {filteredUser.map((p) => (
                  <PromptListItem
                    key={p.id}
                    name={p.name}
                    category={p.category}
                    content={p.content}
                    isActive={p.content === currentContent}
                    onSelect={() => { onSelect(p.content, p.name); onOpenChange(false); }}
                    onEdit={() => { setEditing(p); setCreating(false); }}
                    onDelete={() => handleDelete(p.id)}
                  />
                ))}
              </div>
            </div>
          )}

          {filteredUser.length > 0 && filteredBuiltins.length > 0 && (
            <Separator className="my-3" />
          )}

          {/* Built-in prompts */}
          {filteredBuiltins.length > 0 && (
            <div>
              <p className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wide mb-2 px-1">
                Built-in ({filteredBuiltins.length})
              </p>
              <div className="space-y-0.5">
                {filteredBuiltins.map((p) => (
                  <PromptListItem
                    key={p.id}
                    name={p.name}
                    category={p.category}
                    content={p.content}
                    isBuiltin
                    isActive={p.content === currentContent}
                    onSelect={() => { onSelect(p.content, p.name); onOpenChange(false); }}
                    onFork={() => handleFork(p.id)}
                  />
                ))}
              </div>
            </div>
          )}

          {filteredUser.length === 0 && filteredBuiltins.length === 0 && (
            <p className="text-sm text-muted-foreground text-center py-8">No prompts match your search.</p>
          )}
        </ScrollArea>
      </DialogContent>
    </Dialog>
  );
}

// ── Main export ────────────────────────────────────────────────────────────

export function PromptPicker({
  onSelect,
  currentContent,
  triggerLabel = "System Prompt",
  className,
}: PromptPickerProps) {
  const [dialogOpen, setDialogOpen] = useState(false);

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        className={`gap-1.5 h-7 text-xs ${className ?? ""}`}
        onClick={() => setDialogOpen(true)}
        title="Open system prompt library"
      >
        <BookOpen className="h-3.5 w-3.5" />
        {triggerLabel}
      </Button>

      <PromptManageDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        onSelect={onSelect}
        currentContent={currentContent}
      />
    </>
  );
}

/**
 * Compact inline variant — just a small icon button.
 * Used in tight spaces like the inference settings panel.
 */
export function PromptPickerIcon({
  onSelect,
  currentContent,
}: {
  onSelect: (content: string, name: string) => void;
  currentContent?: string;
}) {
  const [dialogOpen, setDialogOpen] = useState(false);

  return (
    <>
      <button
        className="p-1 rounded hover:bg-muted/80 text-muted-foreground hover:text-foreground transition-colors"
        onClick={() => setDialogOpen(true)}
        title="Open prompt library"
      >
        <BookOpen className="h-3.5 w-3.5" />
      </button>

      <PromptManageDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        onSelect={onSelect}
        currentContent={currentContent}
      />
    </>
  );
}
