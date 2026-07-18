import { useCallback, useEffect, useMemo, useState } from "react";
import { FolderOpen, Gauge, Loader2, Plus, RefreshCw, Save, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  engine,
  type FilesystemIndexStatus,
  type FilesystemIndexingSettings,
  type FilesystemPlaceResponse,
  type FilesystemPriorityRoot,
} from "@/lib/api";

function pathLabel(path: string): string {
  const clean = path.replace(/[\\/]+$/, "");
  const parts = clean.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

async function pickDirectory(): Promise<string | null> {
  try {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const selected = await open({ directory: true, multiple: false });
    return typeof selected === "string" ? selected : null;
  } catch {
    return null;
  }
}

export function authoredPriorityRoots(settings: FilesystemIndexingSettings): FilesystemPriorityRoot[] {
  return settings.priority_roots.map((root) => ({ ...root }));
}

export function indexStateLabel(status: FilesystemIndexStatus | null): string {
  if (!status?.started) return "Waiting";
  if (status.metadata_state === "partial") return "Needs attention";
  if (status.metadata_state === "complete") return "Current";
  return "Indexing";
}

export function FilesystemIndexSettings({ connected }: { connected: boolean }) {
  const [places, setPlaces] = useState<FilesystemPlaceResponse[]>([]);
  const [roots, setRoots] = useState<FilesystemPriorityRoot[]>([]);
  const [status, setStatus] = useState<FilesystemIndexStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);

  const load = useCallback(async () => {
    if (!connected) return;
    setLoading(true);
    setMessage(null);
    try {
      const [placeResult, indexSettings, indexStatus] = await Promise.all([
        engine.getFilesystemPlaces(),
        engine.getFilesystemIndexingSettings(),
        engine.getFilesystemIndexStatus(),
      ]);
      setPlaces(placeResult.places);
      setRoots(authoredPriorityRoots(indexSettings));
      setStatus(indexStatus);
      setDirty(false);
    } catch (reason) {
      setMessage({ text: reason instanceof Error ? reason.message : String(reason), error: true });
    } finally {
      setLoading(false);
    }
  }, [connected]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!connected || status?.index_complete !== false) return;
    const active = (status.directories_ready ?? 0) > 0 || (status.directories_claimed ?? 0) > 0;
    const delay = active ? 5_000 : 30_000;
    const interval = window.setInterval(() => {
      void engine.getFilesystemIndexStatus().then(setStatus).catch(() => undefined);
    }, delay);
    return () => window.clearInterval(interval);
  }, [connected, status?.index_complete, status?.directories_claimed, status?.directories_ready]);

  const addRoot = useCallback(async () => {
    const path = await pickDirectory();
    if (!path) return;
    setRoots((current) => current.some((root) => root.path === path) ? current : [...current, { path, label: pathLabel(path) }]);
    setDirty(true);
  }, []);

  const save = useCallback(async () => {
    setSaving(true);
    setMessage(null);
    try {
      const result = await engine.setFilesystemPriorityRoots(roots);
      setPlaces(result.places);
      const [indexSettings, indexStatus] = await Promise.all([
        engine.getFilesystemIndexingSettings(),
        engine.getFilesystemIndexStatus(),
      ]);
      setRoots(authoredPriorityRoots(indexSettings));
      setStatus(indexStatus);
      setDirty(false);
      window.dispatchEvent(new Event("matrx-filesystem-roots-changed"));
      setMessage({ text: "Priority locations saved. Background indexing has been reprioritized.", error: false });
    } catch (reason) {
      setMessage({ text: reason instanceof Error ? reason.message : String(reason), error: true });
    } finally {
      setSaving(false);
    }
  }, [roots]);

  const discoveredCount = useMemo(() => places.filter((place) => place.available).length, [places]);

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-base"><Gauge className="h-4 w-4 text-primary" /> Filesystem Discovery</CardTitle>
            <p className="mt-1 max-w-3xl text-xs text-muted-foreground">
              Matrx indexes every accessible discovered location in the background. Add priority locations to scan important work sooner and in greater detail; this never excludes other folders or drives.
            </p>
          </div>
          <Button type="button" variant="outline" size="sm" disabled={!connected || loading} onClick={() => void load()}>
            {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />} Refresh
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {!connected ? (
          <p className="py-4 text-center text-sm text-muted-foreground">Connect to the engine to manage filesystem discovery.</p>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
              <Metric value={status?.entries.toLocaleString() ?? "—"} label="Indexed entries" />
              <Metric value={status?.directories_pending.toLocaleString() ?? "—"} label="Directories pending" />
              <Metric value={status?.directories_failed.toLocaleString() ?? "—"} label="Directories blocked" />
              <Metric value={discoveredCount || "—"} label="Available locations" />
              <div className="rounded-md border bg-muted/20 p-3">
                <Badge variant={status?.metadata_state === "complete" ? "success" : status?.metadata_state === "partial" ? "destructive" : "secondary"}>
                  {indexStateLabel(status)}
                </Badge>
                <div className="mt-1 text-[10px] uppercase tracking-wide text-muted-foreground">{status?.fts5 ? "Fast path search enabled" : "Portable search mode"}</div>
              </div>
            </div>

            {status?.metadata_state === "partial" && status.scan_failures.length > 0 && (
              <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs">
                <div className="font-medium text-amber-800 dark:text-amber-200">
                  Some locations could not be indexed. Matrx will retry automatically; check that these folders still exist and that Matrx has permission to read them.
                </div>
                <ul className="mt-2 space-y-1 text-muted-foreground">
                  {status.scan_failures.slice(0, 3).map((failure) => (
                    <li key={failure.path} className="truncate" title={`${failure.path}: ${failure.last_error}`}>
                      <code>{failure.path}</code> — {failure.last_error_kind ?? "unavailable"}
                    </li>
                  ))}
                </ul>
                {status.scan_failures.length > 3 && (
                  <div className="mt-1 text-muted-foreground">And {status.scan_failures.length - 3} more blocked locations.</div>
                )}
              </div>
            )}

            <div className="space-y-2">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <h3 className="text-sm font-medium">Priority locations</h3>
                  <p className="text-xs text-muted-foreground">Useful for source trees, project archives, or other folders you search often.</p>
                </div>
                <Button type="button" variant="outline" size="sm" onClick={() => void addRoot()}><Plus className="h-3.5 w-3.5" /> Add folder</Button>
              </div>
              {roots.length === 0 ? (
                <div className="rounded-md border border-dashed p-4 text-center text-xs text-muted-foreground">
                  No extra priority locations. Matrx is still indexing standard user folders, configured Matrx paths, and accessible volumes.
                </div>
              ) : (
                <div className="space-y-2">
                  {roots.map((root, index) => (
                    <div key={root.path} className="flex items-center gap-2 rounded-md border p-2">
                      <FolderOpen className="h-4 w-4 shrink-0 text-amber-500" />
                      <Input
                        value={root.label ?? ""}
                        aria-label={`Label for ${root.path}`}
                        className="h-8 w-36"
                        onChange={(event) => {
                          const label = event.target.value;
                          setRoots((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, label } : item));
                          setDirty(true);
                        }}
                      />
                      <code className="min-w-0 flex-1 truncate text-xs text-muted-foreground" title={root.path}>{root.path}</code>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8 text-muted-foreground hover:text-destructive"
                        title="Remove priority"
                        onClick={() => {
                          setRoots((current) => current.filter((_, itemIndex) => itemIndex !== index));
                          setDirty(true);
                        }}
                      ><Trash2 className="h-3.5 w-3.5" /></Button>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="flex items-center justify-between gap-3 border-t pt-3">
              <div className={message?.error ? "text-xs text-destructive" : "text-xs text-emerald-600 dark:text-emerald-400"}>{message?.text ?? status?.policy ?? ""}</div>
              <Button type="button" size="sm" disabled={!dirty || saving} onClick={() => void save()}>
                {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />} Save priorities
              </Button>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function Metric({ value, label }: { value: string | number; label: string }) {
  return (
    <div className="rounded-md border bg-muted/20 p-3">
      <div className="text-lg font-semibold tabular-nums">{value}</div>
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
    </div>
  );
}
