import { memo, useMemo, useState } from "react";
import { FileText, Home as HomeIcon, Search } from "lucide-react";
import { cn } from "@/lib/utils";
import type { DocHome } from "@/lib/api";

interface HomesListProps {
  homes: DocHome[];
  activeHomeId: string | null;
  totalCount: number;
  onSelect: (homeScopeId: string | null) => void;
}

/**
 * A simple, searchable list of the person's homes (each organization's
 * General plus scopes). A home is chosen, never created here: directories on
 * disk that match no home are not homes and are not listed.
 */
export const HomesList = memo(function HomesList({
  homes,
  activeHomeId,
  totalCount,
  onSelect,
}: HomesListProps) {
  const [query, setQuery] = useState("");
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return homes
      .filter((h) => h.id !== null && !h.unmatched)
      .filter((h) => !q || h.name.toLowerCase().includes(q));
  }, [homes, query]);

  return (
    <div className="flex flex-col gap-1 text-sm">
      <div className="relative">
        <Search className="pointer-events-none absolute left-2 top-2 h-3.5 w-3.5 text-muted-foreground" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Homes"
          aria-label="Search homes"
          className="h-7 w-full rounded-md border bg-transparent pl-7 pr-2 text-xs outline-none focus-visible:ring-1 focus-visible:ring-ring"
        />
      </div>
      <button
        onClick={() => onSelect(null)}
        className={cn(
          "flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-accent",
          activeHomeId === null && "bg-accent font-medium",
        )}
      >
        <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <span className="truncate">All notes</span>
        <span className="ml-auto text-xs text-muted-foreground">{totalCount}</span>
      </button>
      {visible.map((h) => (
        <button
          key={h.id as string}
          onClick={() => onSelect(h.id)}
          title={h.organization_name ? `${h.home_name} · ${h.organization_name}` : h.home_name}
          className={cn(
            "flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-accent",
            activeHomeId === h.id && "bg-accent font-medium",
          )}
        >
          <HomeIcon className="h-3.5 w-3.5 shrink-0 text-amber-500" />
          <span className="truncate">{h.name}</span>
          <span className="ml-auto text-xs text-muted-foreground">{h.note_count}</span>
        </button>
      ))}
    </div>
  );
});
