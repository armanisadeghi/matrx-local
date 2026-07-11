"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { PageHeader } from "@/components/layout/PageHeader";
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Plus,
  Trash2,
  Copy,
  Check,
  Pin,
  Search,
  BookOpen,
  Lock,
  Sparkles,
  ArrowLeft,
  Save,
  RotateCcw,
  ChevronRight,
  Tag,
  Clock,
  Hash,
  CopyPlus,
} from "lucide-react";
import { cn } from "@/lib/utils";

// ── Types ──────────────────────────────────────────────────────────────────

type PromptEntry =
  | (SystemPrompt & { isBuiltin: false })
  | (Omit<SystemPrompt, "createdAt" | "updatedAt" | "isPinned"> & {
      isBuiltin: true;
      createdAt: undefined;
      updatedAt: undefined;
      isPinned: false;
    });

// ── Helpers ────────────────────────────────────────────────────────────────

function toEntries(userPrompts: SystemPrompt[]): PromptEntry[] {
  const builtins: PromptEntry[] = BUILTIN_PROMPTS.map((p) => ({
    ...p,
    isBuiltin: true as const,
    createdAt: undefined,
    updatedAt: undefined,
    isPinned: false as const,
  }));
  const user: PromptEntry[] = userPrompts.map((p) => ({
    ...p,
    isBuiltin: false as const,
  }));
  // Pinned user prompts float to the top, then built-ins, then rest
  const pinned = user.filter((p) => p.isPinned);
  const unpinned = user.filter((p) => !p.isPinned);
  return [...pinned, ...builtins, ...unpinned];
}

function formatDate(ts: number | undefined): string {
  if (!ts) return "";
  return new Date(ts).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

// ── Editor Panel ───────────────────────────────────────────────────────────

interface EditorState {
  name: string;
  content: string;
  category: string;
}

const CUSTOM_CATEGORY_VALUE = "__custom__";

function PromptEditor({
  initial,
  isBuiltin,
  onSave,
  onCancel,
  onDelete,
  onFork,
  onDuplicate,
}: {
  initial: PromptEntry | null;
  isBuiltin: boolean;
  onSave: (data: EditorState) => void;
  onCancel: () => void;
  onDelete?: () => void;
  onFork?: () => void;
  onDuplicate?: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [content, setContent] = useState(initial?.content ?? "");
  const [category, setCategory] = useState(initial?.category ?? "General");
  const [dirty, setDirty] = useState(false);
  const [copied, setCopied] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const categories = systemPrompts.categories();
  const isNew = initial === null;

  // Whether the current category value matches one of the known options
  const isCustomCategory = category !== "" && !categories.includes(category);
  // The Select value — either the category itself or the sentinel for custom
  const selectValue = isCustomCategory
    ? CUSTOM_CATEGORY_VALUE
    : category || "General";
  const [showCustomInput, setShowCustomInput] = useState(isCustomCategory);

  const canSave =
    name.trim().length > 0 && content.trim().length > 0 && (dirty || isNew);

  const handleChange =
    <T extends string>(setter: (v: T) => void) =>
    (v: T) => {
      setter(v);
      setDirty(true);
    };

  const handleSaveClick = () => {
    onSave({ name, content, category });
    setDirty(false);
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleReset = () => {
    if (!initial) return;
    setName(initial.name);
    setContent(initial.content);
    setCategory(initial.category);
    setDirty(false);
  };

  // Auto-focus the textarea for new prompts, name field for edits
  useEffect(() => {
    if (isNew) {
      setTimeout(() => textareaRef.current?.focus(), 50);
    }
  }, [isNew]);

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b shrink-0">
        <div className="flex items-center gap-3">
          <button
            onClick={onCancel}
            className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
          >
            <ArrowLeft className="h-4 w-4" />
            Back
          </button>
          <Separator orientation="vertical" className="h-4" />
          <h2 className="text-base font-semibold">
            {isBuiltin ? initial?.name : isNew ? "New Prompt" : "Edit Prompt"}
          </h2>
          {isBuiltin && (
            <Badge variant="secondary" className="gap-1">
              <Lock className="h-3 w-3" />
              Built-in
            </Badge>
          )}
          {!isNew && !isBuiltin && dirty && (
            <Badge
              variant="outline"
              className="text-amber-500 border-amber-500/30"
            >
              Unsaved changes
            </Badge>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={handleCopy}
            className="gap-1.5"
          >
            {copied ? (
              <Check className="h-3.5 w-3.5 text-emerald-500" />
            ) : (
              <Copy className="h-3.5 w-3.5" />
            )}
            {copied ? "Copied" : "Copy"}
          </Button>
          {isBuiltin ? (
            <Button size="sm" onClick={onFork} className="gap-1.5">
              <Plus className="h-3.5 w-3.5" />
              Save a Copy
            </Button>
          ) : (
            <>
              {!isNew && dirty && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={handleReset}
                  className="gap-1.5"
                >
                  <RotateCcw className="h-3.5 w-3.5" />
                  Reset
                </Button>
              )}
              {!isNew && onDuplicate && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={onDuplicate}
                  className="gap-1.5"
                >
                  <CopyPlus className="h-3.5 w-3.5" />
                  Duplicate
                </Button>
              )}
              {!isNew && onDelete && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={onDelete}
                  className="gap-1.5 text-destructive hover:text-destructive hover:bg-destructive/10"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  Delete
                </Button>
              )}
              <Button
                size="sm"
                disabled={!canSave}
                onClick={handleSaveClick}
                className="gap-1.5"
              >
                <Save className="h-3.5 w-3.5" />
                {isNew ? "Create Prompt" : "Save Changes"}
              </Button>
            </>
          )}
        </div>
      </div>

      {/* Body */}
      <div className="flex flex-1 min-h-0">
        {/* Main editor */}
        <div className="flex-1 flex flex-col min-w-0 p-6 space-y-5">
          {/* Metadata row */}
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label className="text-sm font-medium">Name</Label>
              {isBuiltin ? (
                <p className="text-sm font-semibold">{initial?.name}</p>
              ) : (
                <Input
                  value={name}
                  onChange={(e) => handleChange(setName)(e.target.value)}
                  placeholder="e.g. Technical Writer, Code Reviewer…"
                  className="h-10"
                  disabled={isBuiltin}
                  autoFocus={!isNew}
                />
              )}
            </div>
            <div className="space-y-2">
              <Label className="text-sm font-medium">Category</Label>
              {isBuiltin ? (
                <p className="text-sm text-muted-foreground">
                  {initial?.category}
                </p>
              ) : (
                <div className="space-y-2">
                  <Select
                    value={selectValue}
                    onValueChange={(val) => {
                      if (val === CUSTOM_CATEGORY_VALUE) {
                        setShowCustomInput(true);
                        handleChange(setCategory)("");
                      } else {
                        setShowCustomInput(false);
                        handleChange(setCategory)(val);
                      }
                    }}
                  >
                    <SelectTrigger className="h-10">
                      <SelectValue placeholder="Select a category…" />
                    </SelectTrigger>
                    <SelectContent>
                      {categories.map((c) => (
                        <SelectItem key={c} value={c}>
                          {c}
                        </SelectItem>
                      ))}
                      <SelectItem value={CUSTOM_CATEGORY_VALUE}>
                        <span className="text-muted-foreground italic">
                          Custom…
                        </span>
                      </SelectItem>
                    </SelectContent>
                  </Select>
                  {showCustomInput && (
                    <Input
                      value={category}
                      onChange={(e) =>
                        handleChange(setCategory)(e.target.value)
                      }
                      placeholder="Enter custom category…"
                      className="h-9"
                      autoFocus
                    />
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Prompt content */}
          <div className="flex-1 flex flex-col space-y-2 min-h-0">
            <div className="flex items-center justify-between">
              <Label className="text-sm font-medium">System Prompt</Label>
              <span className="text-xs text-muted-foreground">
                {content.length} characters
              </span>
            </div>
            <Textarea
              ref={textareaRef}
              value={content}
              onChange={(e) => handleChange(setContent)(e.target.value)}
              placeholder={
                isBuiltin
                  ? ""
                  : "You are a helpful assistant that…\n\nBe specific about the role, tone, constraints, and output format you want."
              }
              className={cn(
                "flex-1 resize-none text-sm leading-relaxed font-mono",
                "min-h-[320px]",
                isBuiltin && "bg-muted/30 cursor-default",
              )}
              readOnly={isBuiltin}
              spellCheck={!isBuiltin}
            />
          </div>

          {/* Metadata footer for existing prompts */}
          {!isNew && initial && (
            <div className="flex items-center gap-4 text-xs text-muted-foreground pt-2 border-t">
              {initial.createdAt && (
                <span className="flex items-center gap-1.5">
                  <Clock className="h-3 w-3" />
                  Created {formatDate(initial.createdAt)}
                </span>
              )}
              {initial.updatedAt && (
                <span className="flex items-center gap-1.5">
                  <Clock className="h-3 w-3" />
                  Updated {formatDate(initial.updatedAt)}
                </span>
              )}
              <span className="flex items-center gap-1.5 ml-auto">
                <Hash className="h-3 w-3" />
                {initial.id}
              </span>
            </div>
          )}
        </div>

        {/* Right tips panel */}
        <div className="w-64 shrink-0 border-l bg-muted/10 p-5 space-y-5">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-3">
              Writing Tips
            </p>
            <ul className="space-y-2.5 text-xs text-muted-foreground">
              <li className="flex gap-2">
                <span className="text-primary shrink-0">→</span>
                <span>
                  Start with the role: <em>"You are an expert…"</em>
                </span>
              </li>
              <li className="flex gap-2">
                <span className="text-primary shrink-0">→</span>
                <span>Specify tone: formal, casual, technical, simple</span>
              </li>
              <li className="flex gap-2">
                <span className="text-primary shrink-0">→</span>
                <span>
                  Define output format: bullet list, JSON, markdown, prose
                </span>
              </li>
              <li className="flex gap-2">
                <span className="text-primary shrink-0">→</span>
                <span>
                  Add constraints: word count, language, what NOT to do
                </span>
              </li>
              <li className="flex gap-2">
                <span className="text-primary shrink-0">→</span>
                <span>Use examples inline if the format is non-obvious</span>
              </li>
            </ul>
          </div>

          <Separator />

          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-3">
              Usage
            </p>
            <ul className="space-y-2.5 text-xs text-muted-foreground">
              <li className="flex gap-2">
                <span className="text-primary shrink-0">1.</span>
                <span>
                  Go to <strong>Confidential Chat → Inference</strong>
                </span>
              </li>
              <li className="flex gap-2">
                <span className="text-primary shrink-0">2.</span>
                <span>Open the settings panel (⚙)</span>
              </li>
              <li className="flex gap-2">
                <span className="text-primary shrink-0">3.</span>
                <span>
                  Click <strong>Choose Prompt</strong> to select from your
                  library
                </span>
              </li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Prompt List Item ───────────────────────────────────────────────────────

function PromptRow({
  prompt,
  isActive,
  onSelect,
  onPin: _onPin,
}: {
  prompt: PromptEntry;
  isActive: boolean;
  onSelect: () => void;
  onPin?: () => void;
}) {
  return (
    <button
      className={cn(
        "w-full text-left flex items-start gap-3 px-4 py-3 rounded-lg transition-colors group",
        isActive
          ? "bg-primary/10 border border-primary/20"
          : "hover:bg-muted/60 border border-transparent",
      )}
      onClick={onSelect}
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          {prompt.isPinned && <Pin className="h-3 w-3 text-primary shrink-0" />}
          <span className="text-sm font-medium truncate">{prompt.name}</span>
          {isActive && (
            <Check className="h-3.5 w-3.5 text-primary shrink-0 ml-auto" />
          )}
        </div>
        <p className="text-xs text-muted-foreground line-clamp-2 leading-relaxed">
          {prompt.content}
        </p>
      </div>
      <ChevronRight
        className={cn(
          "h-4 w-4 text-muted-foreground/40 shrink-0 mt-0.5 transition-colors",
          isActive && "text-primary",
        )}
      />
    </button>
  );
}

// ── Main Page ──────────────────────────────────────────────────────────────

export function SystemPrompts() {
  const [searchParams] = useSearchParams();

  // All user prompts (reactive — reloaded on any mutation)
  const [userPrompts, setUserPrompts] = useState<SystemPrompt[]>(() =>
    systemPrompts.list(),
  );

  // The current entry shown in the editor panel
  const [selected, setSelected] = useState<PromptEntry | null>(null);
  // null = list view, "new" = blank editor, entry = edit/view
  const [mode, setMode] = useState<"list" | "new" | "edit">("list");

  const [search, setSearch] = useState("");
  const [activeCategory, setActiveCategory] = useState<string | null>(null);
  const [successId, setSuccessId] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState("Prompt saved");

  const reload = useCallback(() => setUserPrompts(systemPrompts.list()), []);

  // If navigated here with ?select=<id>, open that prompt for editing immediately
  useEffect(() => {
    const id = searchParams.get("select");
    if (!id) return;
    const user = systemPrompts.get(id);
    if (user) {
      setSelected({ ...user, isBuiltin: false });
      setMode("edit");
      return;
    }
    const builtin = BUILTIN_PROMPTS.find((p) => p.id === id);
    if (builtin) {
      setSelected({
        ...builtin,
        isBuiltin: true,
        createdAt: undefined,
        updatedAt: undefined,
        isPinned: false,
      });
      setMode("edit");
    }
  }, [searchParams]);

  const allEntries = toEntries(userPrompts);
  const categories = systemPrompts.categories();

  const filteredEntries = allEntries.filter((p) => {
    const matchesSearch =
      !search ||
      p.name.toLowerCase().includes(search.toLowerCase()) ||
      p.content.toLowerCase().includes(search.toLowerCase()) ||
      p.category.toLowerCase().includes(search.toLowerCase());
    const matchesCategory = !activeCategory || p.category === activeCategory;
    return matchesSearch && matchesCategory;
  });

  // Group by category for the list display
  const grouped: Record<string, PromptEntry[]> = {};
  filteredEntries.forEach((p) => {
    (grouped[p.category] ??= []).push(p);
  });

  const handleSave = (data: EditorState) => {
    if (mode === "new") {
      const created = systemPrompts.create(data);
      reload();
      setSelected({ ...created, isBuiltin: false });
      setMode("edit");
      setSuccessMessage("Prompt created");
      setSuccessId(created.id);
      setTimeout(() => setSuccessId(null), 2500);
    } else if (mode === "edit" && selected && !selected.isBuiltin) {
      systemPrompts.update(selected.id, data);
      reload();
      setSuccessMessage("Prompt saved");
      setSuccessId(selected.id);
      setTimeout(() => setSuccessId(null), 2500);
      // Refresh selected to show updated timestamps
      const updated = systemPrompts.get(selected.id);
      if (updated) setSelected({ ...updated, isBuiltin: false });
    }
  };

  const handleDelete = () => {
    if (!selected || selected.isBuiltin) return;
    systemPrompts.delete(selected.id);
    reload();
    setSelected(null);
    setMode("list");
  };

  const handleFork = () => {
    if (!selected || !selected.isBuiltin) return;
    const forked = systemPrompts.forkBuiltin(selected.id);
    if (!forked) return;
    reload();
    setSelected({ ...forked, isBuiltin: false });
    setMode("edit");
  };

  const handleDuplicate = () => {
    if (!selected || selected.isBuiltin) return;
    const duped = systemPrompts.duplicate(selected.id);
    if (!duped) return;
    reload();
    // Navigate to the new duplicate, stay in edit mode
    setSelected({ ...duped, isBuiltin: false });
    setSuccessMessage("Prompt duplicated");
    setSuccessId(duped.id);
    setTimeout(() => setSuccessId(null), 2500);
  };

  const handlePin = (id: string) => {
    const p = systemPrompts.get(id);
    if (!p) return;
    systemPrompts.update(id, { isPinned: !p.isPinned });
    reload();
  };

  const handleCancel = () => {
    setSelected(null);
    setMode("list");
  };

  // ── Render ─────────────────────────────────────────────────────────────

  if (mode === "new" || mode === "edit") {
    return (
      <div className="flex h-full flex-col">
        <PromptEditor
          key={selected?.id ?? "new"}
          initial={selected}
          isBuiltin={selected?.isBuiltin ?? false}
          onSave={handleSave}
          onCancel={handleCancel}
          onDelete={handleDelete}
          onFork={handleFork}
          onDuplicate={handleDuplicate}
        />
        {successId && (
          <div className="fixed bottom-6 right-6 z-50 flex items-center gap-2 rounded-lg bg-emerald-500 px-4 py-2.5 text-sm text-white shadow-lg">
            <Check className="h-4 w-4" />
            {successMessage}
          </div>
        )}
      </div>
    );
  }

  // ── List view ────────────────────────────────────────────────────────────

  const userCount = userPrompts.length;
  const builtinCount = BUILTIN_PROMPTS.length;

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="System Prompts"
        description="Create and manage reusable system prompts for confidential chat and cloud models"
      >
        <Button
          onClick={() => {
            setSelected(null);
            setMode("new");
          }}
          className="gap-2"
        >
          <Plus className="h-4 w-4" />
          New Prompt
        </Button>
      </PageHeader>

      <div className="flex flex-1 min-h-0">
        {/* ── Left category sidebar ─────────────────────────────────────── */}
        <div className="w-52 shrink-0 border-r flex flex-col">
          <div className="p-3">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground pointer-events-none" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search…"
                className="pl-8 h-8 text-sm"
              />
            </div>
          </div>
          <ScrollArea className="flex-1">
            <div className="px-2 pb-4 space-y-0.5">
              <button
                className={cn(
                  "w-full flex items-center justify-between px-3 py-2 rounded-lg text-sm transition-colors",
                  !activeCategory
                    ? "bg-primary/10 text-primary font-medium"
                    : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                )}
                onClick={() => setActiveCategory(null)}
              >
                <span className="flex items-center gap-2">
                  <BookOpen className="h-3.5 w-3.5" />
                  All Prompts
                </span>
                <span className="text-xs opacity-70">{allEntries.length}</span>
              </button>

              <Separator className="my-2" />

              {categories.map((cat) => {
                const count = allEntries.filter(
                  (p) => p.category === cat,
                ).length;
                return (
                  <button
                    key={cat}
                    className={cn(
                      "w-full flex items-center justify-between px-3 py-2 rounded-lg text-sm transition-colors",
                      activeCategory === cat
                        ? "bg-primary/10 text-primary font-medium"
                        : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                    )}
                    onClick={() =>
                      setActiveCategory(activeCategory === cat ? null : cat)
                    }
                  >
                    <span className="flex items-center gap-2">
                      <Tag className="h-3.5 w-3.5" />
                      {cat}
                    </span>
                    <span className="text-xs opacity-70">{count}</span>
                  </button>
                );
              })}
            </div>
          </ScrollArea>

          {/* Stats footer */}
          <div className="border-t p-3 space-y-1 text-xs text-muted-foreground">
            <div className="flex justify-between">
              <span>My prompts</span>
              <span className="font-medium text-foreground">{userCount}</span>
            </div>
            <div className="flex justify-between">
              <span>Built-in</span>
              <span className="font-medium text-foreground">
                {builtinCount}
              </span>
            </div>
          </div>
        </div>

        {/* ── Main list ────────────────────────────────────────────────── */}
        <div className="flex-1 flex flex-col min-w-0">
          {/* Toolbar */}
          <div className="flex items-center justify-between px-6 py-3 border-b shrink-0">
            <p className="text-sm text-muted-foreground">
              {activeCategory ? (
                <span>
                  <span className="font-medium text-foreground">
                    {activeCategory}
                  </span>
                  {" · "}
                  {filteredEntries.length} prompt
                  {filteredEntries.length !== 1 ? "s" : ""}
                </span>
              ) : (
                <span>
                  {filteredEntries.length} prompt
                  {filteredEntries.length !== 1 ? "s" : ""}
                  {search && (
                    <span className="ml-1">
                      matching <em>"{search}"</em>
                    </span>
                  )}
                </span>
              )}
            </p>
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Sparkles className="h-3.5 w-3.5" />
              <span>
                Select a prompt to use it in Confidential Chat → Inference
              </span>
            </div>
          </div>

          <ScrollArea className="flex-1">
            <div className="p-6 space-y-8">
              {filteredEntries.length === 0 && (
                <div className="flex flex-col items-center justify-center py-24 text-center">
                  <BookOpen className="h-12 w-12 text-muted-foreground/20 mb-4" />
                  <p className="text-sm font-medium text-muted-foreground mb-1">
                    {search ? "No prompts match your search" : "No prompts yet"}
                  </p>
                  {!search && (
                    <p className="text-xs text-muted-foreground mb-4">
                      Create your first prompt to get started
                    </p>
                  )}
                  {!search && (
                    <Button
                      size="sm"
                      onClick={() => {
                        setSelected(null);
                        setMode("new");
                      }}
                      className="gap-2 mt-2"
                    >
                      <Plus className="h-3.5 w-3.5" />
                      Create Prompt
                    </Button>
                  )}
                </div>
              )}

              {/* User prompts section */}
              {(() => {
                const userEntries = filteredEntries.filter((p) => !p.isBuiltin);
                if (userEntries.length === 0) return null;
                return (
                  <div>
                    <div className="flex items-center gap-3 mb-3">
                      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                        My Prompts
                      </h3>
                      <div className="flex-1 h-px bg-border" />
                      <span className="text-xs text-muted-foreground">
                        {userEntries.length}
                      </span>
                    </div>
                    <div className="grid gap-1">
                      {userEntries.map((p) => (
                        <div key={p.id} className="group relative">
                          <PromptRow
                            prompt={p}
                            isActive={false}
                            onSelect={() => {
                              setSelected(p);
                              setMode("edit");
                            }}
                          />
                          {/* Pin button on hover */}
                          <button
                            className={cn(
                              "absolute right-9 top-1/2 -translate-y-1/2 p-1 rounded transition-all",
                              "opacity-0 group-hover:opacity-100",
                              p.isPinned
                                ? "text-primary"
                                : "text-muted-foreground hover:text-foreground",
                            )}
                            onClick={(e) => {
                              e.stopPropagation();
                              handlePin(p.id);
                            }}
                            title={p.isPinned ? "Unpin" : "Pin to top"}
                          >
                            <Pin className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })()}

              {/* Built-in prompts section */}
              {(() => {
                const builtinEntries = filteredEntries.filter(
                  (p) => p.isBuiltin,
                );
                if (builtinEntries.length === 0) return null;
                return (
                  <div>
                    <div className="flex items-center gap-3 mb-3">
                      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                        Built-in
                      </h3>
                      <div className="flex-1 h-px bg-border" />
                      <span className="text-xs text-muted-foreground">
                        {builtinEntries.length}
                      </span>
                    </div>
                    <div className="grid gap-1">
                      {builtinEntries.map((p) => (
                        <div key={p.id} className="group relative">
                          <PromptRow
                            prompt={p}
                            isActive={false}
                            onSelect={() => {
                              setSelected(p);
                              setMode("edit");
                            }}
                          />
                          {/* Fork button on hover */}
                          <div className="absolute right-9 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 flex items-center gap-1">
                            <button
                              className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground px-2 py-1 rounded hover:bg-muted/60 transition-colors"
                              onClick={(e) => {
                                e.stopPropagation();
                                const forked = systemPrompts.forkBuiltin(p.id);
                                if (forked) {
                                  reload();
                                  setSelected({ ...forked, isBuiltin: false });
                                  setMode("edit");
                                }
                              }}
                              title="Save a copy to your library"
                            >
                              <Plus className="h-3 w-3" />
                              Copy
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })()}
            </div>
          </ScrollArea>
        </div>
      </div>
    </div>
  );
}
