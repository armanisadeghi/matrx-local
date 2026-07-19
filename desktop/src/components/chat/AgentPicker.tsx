import { useState, useMemo, useRef, useEffect, useCallback } from "react";
import {
  Search,
  X,
  Check,
  Star,
  User,
  Cpu,
  Users,
  SlidersHorizontal,
  RotateCcw,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { AgentInfo, AgentSource } from "@/types/agents";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

// ─── Types ──────────────────────────────────────────────────────────────────

/** Matches admin prompts filter: include rows with no category / no tags. */
const NONE_SENTINEL = "__none__";

type SortOption =
  | "updated-desc"
  | "created-desc"
  | "name-asc"
  | "name-desc"
  | "source"
  | "category-asc";
type SourceFilter = "all" | AgentSource;
type FavFilter = "all" | "yes" | "no";

interface AgentPickerProps {
  agents: AgentInfo[];
  selectedAgentId: string | null;
  onSelect: (agentId: string | null) => void;
  isLoading?: boolean;
  /** Controlled open state */
  open: boolean;
  onClose: () => void;
}

// ─── Constants ───────────────────────────────────────────────────────────────

const SOURCE_LABELS: Record<AgentSource, string> = {
  /** Public catalog from `prompt_builtins` (synced from AIDream). */
  builtin: "Catalog",
  user: "Mine",
  shared: "Shared",
};

const SOURCE_ICONS: Record<AgentSource, React.ReactNode> = {
  builtin: <Cpu className="h-4 w-4" />,
  user: <User className="h-4 w-4" />,
  shared: <Users className="h-4 w-4" />,
};

const SORT_OPTIONS: { value: SortOption; label: string }[] = [
  { value: "updated-desc", label: "Recently updated" },
  { value: "created-desc", label: "Recently created" },
  { value: "name-asc", label: "Name (A–Z)" },
  { value: "name-desc", label: "Name (Z–A)" },
  { value: "category-asc", label: "Category (A–Z)" },
  { value: "source", label: "By source" },
];

const FAV_OPTIONS: { value: FavFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "yes", label: "Favorites only" },
  { value: "no", label: "Not favorites" },
];

// ─── Search scoring (aligned with matrx-admin PromptsGrid weights, agent fields) ─

function computeAgentSearchScore(agent: AgentInfo, token: string): number {
  const q = token.toLowerCase();
  if (!q) return 0;
  let score = 0;
  const name = (agent.name ?? "").toLowerCase();
  const desc = (agent.description ?? "").toLowerCase();

  if (name === q) score += 10000;
  else if (name.startsWith(q)) score += 5000;
  else if (name.includes(q)) score += 2000;

  if (desc === q) score += 1000;
  else if (desc.includes(q)) score += 500;

  if (agent.category?.toLowerCase().includes(q)) score += 300;
  if (agent.tags?.some((t) => t.toLowerCase().includes(q))) score += 300;
  if (agent.source.toLowerCase().includes(q)) score += 150;
  if (agent.id?.toLowerCase().includes(q)) score += 50;

  if (
    agent.variable_defaults?.some(
      (v) =>
        v.name?.toLowerCase().includes(q) ||
        v.defaultValue?.toLowerCase().includes(q) ||
        v.helpText?.toLowerCase().includes(q),
    )
  )
    score += 10;

  return score;
}

function agentMatchesSearch(agent: AgentInfo, query: string): boolean {
  const trimmed = query.trim().toLowerCase();
  if (!trimmed) return true;
  const tokens = trimmed.split(/\s+/).filter(Boolean);
  return tokens.every((t) => computeAgentSearchScore(agent, t) > 0);
}

function totalSearchScore(agent: AgentInfo, query: string): number {
  const trimmed = query.trim().toLowerCase();
  if (!trimmed) return 0;
  const tokens = trimmed.split(/\s+/).filter(Boolean);
  return tokens.reduce((sum, t) => sum + computeAgentSearchScore(agent, t), 0);
}

function readTime(value?: string | null): number {
  if (!value) return 0;
  const time = Date.parse(value);
  return Number.isFinite(time) ? time : 0;
}

// ─── Highlight helper ────────────────────────────────────────────────────────

function highlight(text: string, query: string): React.ReactNode {
  if (!query) return text;
  const idx = text.toLowerCase().indexOf(query.toLowerCase());
  if (idx === -1) return text;
  return (
    <>
      {text.slice(0, idx)}
      <mark className="bg-primary/20 text-primary rounded-[2px] font-medium not-italic">
        {text.slice(idx, idx + query.length)}
      </mark>
      {text.slice(idx + query.length)}
    </>
  );
}

// ─── AgentCard (desktop-friendly grid tile) ─────────────────────────────────

function AgentCard({
  agent,
  isSelected,
  searchQuery,
  onClick,
}: {
  agent: AgentInfo;
  isSelected: boolean;
  searchQuery: string;
  onClick: () => void;
}) {
  const tags = agent.tags?.filter(Boolean) ?? [];

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex h-full min-h-[140px] flex-col rounded-xl border border-border/80 bg-card/40 p-4 text-left shadow-sm transition-all",
        "hover:border-primary/35 hover:bg-accent/25 hover:shadow-md",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
        isSelected &&
          "border-primary/60 bg-primary/5 ring-2 ring-primary/40 shadow-md",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <span
          className={cn(
            "shrink-0 rounded-lg bg-muted/60 p-2",
            agent.source === "builtin" && "text-blue-500",
            agent.source === "user" && "text-emerald-500",
            agent.source === "shared" && "text-violet-500",
          )}
        >
          {SOURCE_ICONS[agent.source]}
        </span>
        <span className="rounded-full border border-border/60 bg-muted/30 px-2.5 py-0.5 text-[11px] font-medium text-muted-foreground">
          {SOURCE_LABELS[agent.source]}
        </span>
      </div>

      <div className="mt-3 flex min-w-0 items-start gap-2">
        <span className="line-clamp-2 text-base font-semibold leading-snug text-foreground">
          {highlight(agent.name, searchQuery)}
        </span>
        {agent.is_favorite && (
          <Star className="mt-0.5 h-4 w-4 shrink-0 fill-amber-400 text-amber-400" />
        )}
      </div>

      {agent.description ? (
        <p className="mt-2 line-clamp-3 text-sm leading-relaxed text-muted-foreground">
          {highlight(agent.description, searchQuery)}
        </p>
      ) : (
        <p className="mt-2 text-sm italic text-muted-foreground/70">
          No description
        </p>
      )}

      {tags.length > 0 && (
        <div className="mt-auto flex flex-wrap gap-1.5 pt-3">
          {tags.slice(0, 6).map((tag) => (
            <span
              key={tag}
              className="rounded-md bg-muted/70 px-2 py-0.5 text-xs text-muted-foreground"
            >
              {highlight(tag, searchQuery)}
            </span>
          ))}
          {tags.length > 6 && (
            <span className="self-center text-xs text-muted-foreground">
              +{tags.length - 6} more
            </span>
          )}
        </div>
      )}

      {isSelected && (
        <div className="mt-3 flex items-center gap-2 border-t border-border/50 pt-3 text-sm font-medium text-primary">
          <Check className="h-4 w-4 shrink-0" />
          Current selection
        </div>
      )}
    </button>
  );
}

// ─── Main component ──────────────────────────────────────────────────────────

export function AgentPicker({
  agents,
  selectedAgentId,
  onSelect,
  isLoading = false,
  open,
  onClose,
}: AgentPickerProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("user");
  const [sortBy, setSortBy] = useState<SortOption>("updated-desc");
  const [favoritesFirst, setFavoritesFirst] = useState(true);
  const [favFilter, setFavFilter] = useState<FavFilter>("all");
  /** Inclusion model (empty = no filter), with NONE_SENTINEL for uncategorized / untagged. */
  const [includedCategories, setIncludedCategories] = useState<string[]>([]);
  const [includedTags, setIncludedTags] = useState<string[]>([]);

  const searchRef = useRef<HTMLInputElement>(null);

  // Focus search on open
  useEffect(() => {
    if (open) {
      setTimeout(() => searchRef.current?.focus(), 50);
    } else {
      setSearchQuery("");
    }
  }, [open]);

  // Derived: available source options from actual data
  const availableSources = useMemo(() => {
    const sources = new Set(agents.map((a) => a.source));
    return (["builtin", "user", "shared"] as AgentSource[]).filter((s) =>
      sources.has(s),
    );
  }, [agents]);

  // All unique categories
  const allCategories = useMemo(() => {
    const cats = new Set<string>();
    agents.forEach((a) => {
      if (a.category) cats.add(a.category);
    });
    return Array.from(cats).sort();
  }, [agents]);

  const allTags = useMemo(() => {
    const tags = new Set<string>();
    agents.forEach((a) => {
      a.tags?.forEach((t) => {
        if (t) tags.add(t);
      });
    });
    return Array.from(tags).sort();
  }, [agents]);

  const hasActiveFilters =
    sourceFilter !== "user" ||
    sortBy !== "updated-desc" ||
    includedCategories.length > 0 ||
    includedTags.length > 0 ||
    favFilter !== "all" ||
    !favoritesFirst;

  const resetFilters = useCallback(() => {
    setSourceFilter("user");
    setSortBy("updated-desc");
    setIncludedCategories([]);
    setIncludedTags([]);
    setFavFilter("all");
    setFavoritesFirst(true);
  }, []);

  const compareAgents = useCallback(
    (a: AgentInfo, b: AgentInfo): number => {
      switch (sortBy) {
        case "updated-desc": {
          const diff = readTime(b.updated_at) - readTime(a.updated_at);
          return diff !== 0 ? diff : a.name.localeCompare(b.name);
        }
        case "created-desc": {
          const diff = readTime(b.created_at) - readTime(a.created_at);
          return diff !== 0 ? diff : a.name.localeCompare(b.name);
        }
        case "name-desc":
          return b.name.localeCompare(a.name);
        case "category-asc": {
          const ca = (a.category ?? "\uffff").toLowerCase();
          const cb = (b.category ?? "\uffff").toLowerCase();
          const c = ca.localeCompare(cb);
          return c !== 0 ? c : a.name.localeCompare(b.name);
        }
        case "source": {
          const order: Record<AgentSource, number> = {
            builtin: 0,
            user: 1,
            shared: 2,
          };
          const diff = order[a.source] - order[b.source];
          return diff !== 0 ? diff : a.name.localeCompare(b.name);
        }
        case "name-asc":
        default:
          return a.name.localeCompare(b.name);
      }
    },
    [sortBy],
  );

  // Filtered + sorted agents
  const filtered = useMemo(() => {
    const q = searchQuery.trim();

    let result = agents.filter((a) => {
      if (sourceFilter !== "all" && a.source !== sourceFilter) return false;

      if (favFilter === "yes" && !a.is_favorite) return false;
      if (favFilter === "no" && a.is_favorite) return false;

      if (includedCategories.length > 0) {
        const cat = a.category ?? null;
        const isUncategorized = !cat;
        if (isUncategorized) {
          if (!includedCategories.includes(NONE_SENTINEL)) return false;
        } else if (!includedCategories.includes(cat)) {
          return false;
        }
      }

      if (includedTags.length > 0) {
        const isUntagged = !a.tags?.length;
        if (isUntagged) {
          if (!includedTags.includes(NONE_SENTINEL)) return false;
        } else if (!a.tags?.some((t) => includedTags.includes(t))) {
          return false;
        }
      }

      if (q && !agentMatchesSearch(a, q)) return false;
      return true;
    });

    result = [...result];
    if (q) {
      result.sort((a, b) => {
        const sa = totalSearchScore(a, q);
        const sb = totalSearchScore(b, q);
        if (sb !== sa) return sb - sa;
        return compareAgents(a, b);
      });
    } else {
      result.sort((a, b) => {
        if (favoritesFirst && favFilter === "all") {
          const aFav = a.is_favorite ? 1 : 0;
          const bFav = b.is_favorite ? 1 : 0;
          if (bFav !== aFav) return bFav - aFav;
        }
        return compareAgents(a, b);
      });
    }

    return result;
  }, [
    agents,
    searchQuery,
    sourceFilter,
    compareAgents,
    includedCategories,
    includedTags,
    favFilter,
    favoritesFirst,
  ]);

  // Group by source when sort === "source" or by category otherwise
  const grouped = useMemo(() => {
    if (sortBy === "source") {
      const groups: { label: string; icon: React.ReactNode; items: AgentInfo[] }[] =
        [];
      const bySource = new Map<AgentSource, AgentInfo[]>();
      filtered.forEach((a) => {
        const arr = bySource.get(a.source) ?? [];
        arr.push(a);
        bySource.set(a.source, arr);
      });
      (["builtin", "user", "shared"] as AgentSource[]).forEach((src) => {
        const items = bySource.get(src);
        if (items?.length) {
          groups.push({
            label: SOURCE_LABELS[src],
            icon: SOURCE_ICONS[src],
            items,
          });
        }
      });
      return groups;
    }

    // Flat list when not grouping by source
    return null;
  }, [filtered, sortBy]);

  const hasUncategorized = useMemo(
    () => agents.some((a) => !a.category),
    [agents],
  );
  const hasUntagged = useMemo(
    () => agents.some((a) => !a.tags?.length),
    [agents],
  );

  const toggleIncluded = useCallback(
    (list: string[], setList: (v: string[]) => void, val: string) => {
      if (list.includes(val)) setList(list.filter((x) => x !== val));
      else setList([...list, val]);
    },
    [],
  );

  const activeFilterCount =
    (sourceFilter !== "user" ? 1 : 0) +
    (sortBy !== "updated-desc" ? 1 : 0) +
    includedCategories.length +
    includedTags.length +
    (favFilter !== "all" ? 1 : 0) +
    (!favoritesFirst ? 1 : 0);

  const pickAgent = (id: string | null) => {
    onSelect(id);
    onClose();
  };

  const renderAgentGrid = (items: AgentInfo[]) => (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {items.map((agent) => (
        <AgentCard
          key={agent.id}
          agent={agent}
          isSelected={selectedAgentId === agent.id}
          searchQuery={searchQuery}
          onClick={() => pickAgent(agent.id)}
        />
      ))}
    </div>
  );

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogContent
        className={cn(
          "flex h-[min(88vh,920px)] w-[min(1240px,calc(100vw-2rem))] max-w-none flex-col gap-0 overflow-hidden p-0 sm:rounded-2xl",
          "data-[state=open]:animate-in data-[state=closed]:animate-out",
        )}
        onOpenAutoFocus={(e) => {
          e.preventDefault();
          searchRef.current?.focus();
        }}
      >
        {/* Leave room for Radix close button */}
        <DialogHeader className="shrink-0 space-y-1 border-b border-border/60 px-6 pb-4 pt-6 pr-14 text-left">
          <DialogTitle className="text-xl font-semibold tracking-tight">
            Choose an agent
          </DialogTitle>
          <DialogDescription className="text-sm text-muted-foreground">
            Browse the catalog and your agents. Use search and filters to narrow
            the list — this window uses your full screen space.
          </DialogDescription>
          <div className="relative pt-3">
            <Search className="pointer-events-none absolute left-3.5 top-[calc(0.75rem+11px)] h-5 w-5 -translate-y-1/2 text-muted-foreground" />
            <input
              ref={searchRef}
              type="search"
              placeholder="Search by name, description, tags, or variable names…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="h-12 w-full rounded-xl border border-border/80 bg-background/80 pl-11 pr-10 text-base text-foreground shadow-sm placeholder:text-muted-foreground/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
            {searchQuery ? (
              <button
                type="button"
                onClick={() => setSearchQuery("")}
                className="absolute right-3 top-[calc(0.75rem+11px)] -translate-y-1/2 rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                aria-label="Clear search"
              >
                <X className="h-4 w-4" />
              </button>
            ) : null}
          </div>
          {availableSources.length >= 1 ? (
            <div className="flex flex-wrap items-center gap-2 pt-3">
              <SlidersHorizontal className="h-4 w-4 text-muted-foreground" />
              <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Show
              </span>
              <SourceChip
                label="All"
                active={sourceFilter === "all"}
                onClick={() => setSourceFilter("all")}
              />
              {availableSources.map((src) => (
                <SourceChip
                  key={src}
                  label={SOURCE_LABELS[src]}
                  icon={SOURCE_ICONS[src]}
                  active={sourceFilter === src}
                  onClick={() => setSourceFilter(src)}
                />
              ))}
              {activeFilterCount > 0 ? (
                <span className="ml-1 rounded-full bg-primary/15 px-2 py-0.5 text-xs font-medium text-primary">
                  {activeFilterCount} filter{activeFilterCount !== 1 ? "s" : ""}
                </span>
              ) : null}
            </div>
          ) : null}
        </DialogHeader>

        <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
          {/* Filters sidebar — always visible on desktop */}
          <aside className="flex max-h-[32vh] shrink-0 flex-col gap-4 overflow-y-auto border-b border-border/60 bg-muted/15 px-4 py-4 lg:max-h-none lg:w-[min(100%,320px)] lg:border-b-0 lg:border-r lg:px-5">
            <div className="flex items-center justify-between gap-2">
              <h3 className="text-sm font-semibold text-foreground">Filters</h3>
              {hasActiveFilters ? (
                <button
                  type="button"
                  onClick={resetFilters}
                  className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                >
                  <RotateCcw className="h-3 w-3" />
                  Reset
                </button>
              ) : null}
            </div>

            <div>
              <label
                htmlFor="agent-picker-sort"
                className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-muted-foreground"
              >
                Sort
              </label>
              <Select
                value={sortBy}
                onValueChange={(value) => setSortBy(value as SortOption)}
              >
                <SelectTrigger id="agent-picker-sort" className="h-10 rounded-lg">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SORT_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div>
              <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Favorites
              </p>
              <div className="flex flex-col gap-1.5">
                {FAV_OPTIONS.map((opt) => (
                  <button
                    type="button"
                    key={opt.value}
                    onClick={() => setFavFilter(opt.value)}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm transition-colors",
                      favFilter === opt.value
                        ? "border-primary/50 bg-primary/10 font-medium text-primary"
                        : "border-border/80 bg-background/50 text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                    )}
                  >
                    {opt.value === "yes" ? (
                      <Star className="h-3.5 w-3.5 shrink-0 fill-amber-400 text-amber-400" />
                    ) : (
                      <span className="w-3.5 shrink-0" />
                    )}
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>

            <button
              type="button"
              onClick={() => setFavoritesFirst((v) => !v)}
              disabled={favFilter !== "all"}
              className={cn(
                "flex w-full items-center justify-center gap-2 rounded-lg border px-3 py-2.5 text-sm transition-colors",
                favFilter !== "all" && "cursor-not-allowed opacity-40",
                favoritesFirst && favFilter === "all"
                  ? "border-amber-400/50 bg-amber-400/10 font-medium text-amber-700 dark:text-amber-300"
                  : "border-border bg-background/50 text-muted-foreground hover:bg-muted/60",
              )}
            >
              <Star
                className={cn(
                  "h-4 w-4",
                  favoritesFirst && favFilter === "all"
                    ? "fill-amber-400 text-amber-400"
                    : "",
                )}
              />
              Pin favorites to top
            </button>

            {(allCategories.length > 0 || hasUncategorized) && (
              <div>
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Category
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {hasUncategorized && (
                    <button
                      type="button"
                      onClick={() =>
                        toggleIncluded(
                          includedCategories,
                          setIncludedCategories,
                          NONE_SENTINEL,
                        )
                      }
                      className={cn(
                        "rounded-lg border px-2.5 py-1 text-xs transition-colors italic",
                        includedCategories.includes(NONE_SENTINEL)
                          ? "border-primary/50 bg-primary/10 text-primary"
                          : "border-border bg-background/60 text-muted-foreground hover:bg-muted/80",
                      )}
                    >
                      No category
                    </button>
                  )}
                  {allCategories.map((cat) => {
                    const on = includedCategories.includes(cat);
                    return (
                      <button
                        type="button"
                        key={cat}
                        onClick={() =>
                          toggleIncluded(
                            includedCategories,
                            setIncludedCategories,
                            cat,
                          )
                        }
                        className={cn(
                          "rounded-lg border px-2.5 py-1 text-xs transition-colors",
                          on
                            ? "border-primary/50 bg-primary/10 font-medium text-primary"
                            : "border-border bg-background/60 text-muted-foreground hover:bg-muted/80",
                        )}
                      >
                        {cat}
                      </button>
                    );
                  })}
                </div>
                {includedCategories.length > 0 && (
                  <button
                    type="button"
                    onClick={() => setIncludedCategories([])}
                    className="mt-2 text-xs font-medium text-primary hover:underline"
                  >
                    Clear categories
                  </button>
                )}
              </div>
            )}

            {(allTags.length > 0 || hasUntagged) && (
              <div>
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Tags
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {hasUntagged && (
                    <button
                      type="button"
                      onClick={() =>
                        toggleIncluded(includedTags, setIncludedTags, NONE_SENTINEL)
                      }
                      className={cn(
                        "rounded-lg border px-2.5 py-1 text-xs transition-colors italic",
                        includedTags.includes(NONE_SENTINEL)
                          ? "border-primary/50 bg-primary/10 text-primary"
                          : "border-border bg-background/60 text-muted-foreground hover:bg-muted/80",
                      )}
                    >
                      No tags
                    </button>
                  )}
                  {allTags.map((tag) => {
                    const on = includedTags.includes(tag);
                    return (
                      <button
                        type="button"
                        key={tag}
                        onClick={() =>
                          toggleIncluded(includedTags, setIncludedTags, tag)
                        }
                        className={cn(
                          "rounded-lg border px-2.5 py-1 text-xs transition-colors",
                          on
                            ? "border-primary/50 bg-primary/10 font-medium text-primary"
                            : "border-border bg-background/60 text-muted-foreground hover:bg-muted/80",
                        )}
                      >
                        {tag}
                      </button>
                    );
                  })}
                </div>
                {includedTags.length > 0 && (
                  <button
                    type="button"
                    onClick={() => setIncludedTags([])}
                    className="mt-2 text-xs font-medium text-primary hover:underline"
                  >
                    Clear tags
                  </button>
                )}
              </div>
            )}
          </aside>

          {/* Main grid */}
          <main className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6">
            <button
              type="button"
              onClick={() => pickAgent(null)}
              className={cn(
                "mb-6 flex w-full flex-col rounded-xl border-2 border-dashed px-5 py-4 text-left transition-colors sm:flex-row sm:items-center sm:justify-between",
                selectedAgentId === null
                  ? "border-primary/50 bg-primary/5"
                  : "border-border/80 bg-muted/10 hover:border-muted-foreground/30 hover:bg-muted/20",
              )}
            >
              <div>
                <span className="text-base font-semibold">No agent</span>
                <p className="mt-1 text-sm text-muted-foreground">
                  Plain chat with the model only — no guided prompt or variables.
                </p>
              </div>
              {selectedAgentId === null ? (
                <Check className="mt-3 h-6 w-6 shrink-0 text-primary sm:mt-0" />
              ) : null}
            </button>

            {isLoading && (
              <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
                <p className="text-base">Loading agents…</p>
              </div>
            )}

            {!isLoading && agents.length === 0 && (
              <div className="py-20 text-center text-base text-muted-foreground">
                No agents available yet. Sign in and sync, or check the engine
                connection.
              </div>
            )}

            {!isLoading && agents.length > 0 && filtered.length === 0 && (
              <div className="py-20 text-center text-base text-muted-foreground">
                No agents match your search or filters.
                {hasActiveFilters ? (
                  <>
                    {" "}
                    <button
                      type="button"
                      className="font-medium text-primary underline-offset-4 hover:underline"
                      onClick={resetFilters}
                    >
                      Clear filters
                    </button>
                  </>
                ) : null}
              </div>
            )}

            {!isLoading &&
              filtered.length > 0 &&
              (searchQuery.trim() || hasActiveFilters) && (
                <p className="mb-4 text-sm text-muted-foreground">
                  Showing{" "}
                  <span className="font-medium text-foreground">
                    {filtered.length}
                  </span>{" "}
                  of {agents.length} agents
                </p>
              )}

            {!isLoading &&
              grouped &&
              grouped.map((group) => (
                <section key={group.label} className="mb-8 last:mb-0">
                  <div className="mb-3 flex items-center gap-2 border-b border-border/50 pb-2">
                    <span className="text-muted-foreground">{group.icon}</span>
                    <h2 className="text-sm font-bold uppercase tracking-wider text-muted-foreground">
                      {group.label}
                    </h2>
                    <span className="text-sm text-muted-foreground">
                      ({group.items.length})
                    </span>
                  </div>
                  {renderAgentGrid(group.items)}
                </section>
              ))}

            {!isLoading && !grouped && filtered.length > 0
              ? renderAgentGrid(filtered)
              : null}
          </main>
        </div>

        {!isLoading && agents.length > 0 ? (
          <footer className="shrink-0 border-t border-border/60 bg-muted/10 px-6 py-2.5 text-xs text-muted-foreground">
            {agents.length} agent{agents.length !== 1 ? "s" : ""} available
            {availableSources.map((src) => {
              const count = agents.filter((a) => a.source === src).length;
              return count > 0 ? (
                <span key={src} className="ml-2">
                  · {count} {SOURCE_LABELS[src].toLowerCase()}
                </span>
              ) : null;
            })}
          </footer>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

// ─── SourceChip ──────────────────────────────────────────────────────────────

function SourceChip({
  label,
  icon,
  active,
  onClick,
}: {
  label: string;
  icon?: React.ReactNode;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors",
        active
          ? "border-primary/50 bg-primary/10 text-primary"
          : "border-border/80 bg-background/60 text-muted-foreground hover:bg-muted/80 hover:text-foreground",
      )}
    >
      {icon && <span className="opacity-80 [&_svg]:h-3.5 [&_svg]:w-3.5">{icon}</span>}
      {label}
    </button>
  );
}
