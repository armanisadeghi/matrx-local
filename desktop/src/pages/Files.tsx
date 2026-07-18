import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowUp, Folder, Loader2, RefreshCw, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FilesystemResultController } from "@/features/filesystem/FilesystemResultController";
import { normalizeFilesystemPayload } from "@/features/filesystem/tool-results";
import type { FilesystemResult } from "@/features/filesystem/types";
import { useFilesystemPlaces } from "@/features/filesystem/use-filesystem-places";
import type { EngineStatus } from "@/hooks/use-engine";
import { engine } from "@/lib/api";

function parentPath(path: string): string | null {
  const clean = path.replace(/[\\/]+$/, "");
  const split = Math.max(clean.lastIndexOf("/"), clean.lastIndexOf("\\"));
  if (split < 0) return null;
  if (split === 0) return clean.slice(0, 1);
  if (/^[A-Za-z]:$/.test(clean.slice(0, split))) return `${clean.slice(0, split)}\\`;
  return clean.slice(0, split);
}

export function Files({ engineStatus }: { engineStatus: EngineStatus }) {
  const connected = engineStatus === "connected";
  const [placesState, placesActions] = useFilesystemPlaces();
  const [result, setResult] = useState<FilesystemResult | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [indexState, setIndexState] = useState<"complete" | "indexing" | "partial" | null>(null);

  const browse = useCallback(async (path: string) => {
    if (!connected) return;
    setLoading(true);
    setError(null);
    try {
      const normalized = normalizeFilesystemPayload(await engine.listFilesystem(path, { limit: 100 }));
      if (normalized?.kind !== "filesystem.directory-page") throw new Error("The engine returned an invalid directory page.");
      setResult(normalized);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, [connected]);

  const search = useCallback(async () => {
    const clean = query.trim();
    if (!connected || !clean) return;
    setLoading(true);
    setError(null);
    try {
      const normalized = normalizeFilesystemPayload(await engine.findFilesystem(clean, { limit: 100 }));
      if (normalized?.kind !== "filesystem.search-page") throw new Error("The engine returned an invalid search page.");
      setResult(normalized);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, [connected, query]);

  useEffect(() => {
    if (!connected) return;
    void engine.getFilesystemIndexStatus().then((status) => setIndexState(status.metadata_state)).catch(() => undefined);
  }, [connected]);

  useEffect(() => {
    if (!connected || result || placesState.loading) return;
    const initial = placesState.places.find((place) => place.available !== false);
    if (initial) void browse(initial.path);
  }, [browse, connected, placesState.loading, placesState.places, result]);

  const currentPath = result?.kind === "filesystem.directory-page" ? result.path : null;
  const up = useMemo(() => currentPath ? parentPath(currentPath) : null, [currentPath]);

  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      <header className="border-b px-5 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold">This Device</h1>
            <p className="text-xs text-muted-foreground">Browse and search the files available to Matrx on this computer.</p>
          </div>
          <form className="flex min-w-64 flex-1 justify-end gap-2" onSubmit={(event) => { event.preventDefault(); void search(); }}>
            <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search names and paths" className="max-w-md" />
            <Button type="submit" variant="outline" disabled={!connected || !query.trim() || loading}>
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />} Search
            </Button>
          </form>
        </div>
        {indexState && indexState !== "complete" && (
          <div className="mt-3 rounded-md border border-sky-500/30 bg-sky-500/10 px-3 py-2 text-xs text-sky-900 dark:text-sky-100">
            Files work immediately while Matrx privately improves its local index in the background.
            {indexState === "partial" && " Some protected or unavailable folders need attention in Settings → Storage."}
          </div>
        )}
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className="w-60 shrink-0 overflow-y-auto border-r p-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Locations</span>
            <Button type="button" variant="ghost" size="icon" className="h-7 w-7" onClick={() => void placesActions.refresh()}>
              <RefreshCw className="h-3.5 w-3.5" />
            </Button>
          </div>
          {placesState.loading ? (
            <div className="flex items-center gap-2 p-2 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading…</div>
          ) : placesState.error ? (
            <div className="p-2 text-xs text-destructive">{placesState.error}</div>
          ) : placesState.places.map((place) => (
            <button key={place.id} type="button" className="flex w-full items-center gap-2 rounded px-2 py-2 text-left text-sm hover:bg-muted" onClick={() => void browse(place.path)}>
              <Folder className="h-4 w-4 shrink-0 text-amber-500" />
              <span className="min-w-0 flex-1 truncate">{place.label}</span>
            </button>
          ))}
        </aside>

        <main className="min-w-0 flex-1 overflow-y-auto p-4">
          <div className="mb-3 flex items-center gap-2">
            <Button type="button" variant="outline" size="sm" disabled={!up || loading} onClick={() => up && void browse(up)}>
              <ArrowUp className="h-3.5 w-3.5" /> Up
            </Button>
            {currentPath && <code className="min-w-0 flex-1 truncate text-xs text-muted-foreground" title={currentPath}>{currentPath}</code>}
          </div>
          {!connected ? (
            <div className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">Connect to the engine to browse this device.</div>
          ) : error ? (
            <div className="rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">{error}</div>
          ) : result ? (
            <div className="overflow-hidden rounded-lg border">
              <FilesystemResultController result={result} onNavigate={(path) => void browse(path)} />
            </div>
          ) : (
            <div className="flex items-center justify-center p-12 text-sm text-muted-foreground"><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Loading files…</div>
          )}
        </main>
      </div>
    </div>
  );
}
