import { useCallback, useEffect, useMemo, useState } from "react";
import { CirclePause, CirclePlay, Eraser, FolderOpen, Gauge, Loader2, Plus, RefreshCw, RotateCcw, Save, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
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
  if (status.metadata_state === "paused") return "Paused";
  return "Indexing";
}

type IndexingPolicy = Omit<FilesystemIndexingSettings, "priority_roots" | "paused">;

export function setContentPolicy(policy: IndexingPolicy, enabled: boolean): IndexingPolicy {
  return { ...policy, content_enabled: enabled, semantic_enabled: enabled && policy.semantic_enabled };
}

export function FilesystemIndexSettings({ connected }: { connected: boolean }) {
  const [places, setPlaces] = useState<FilesystemPlaceResponse[]>([]);
  const [roots, setRoots] = useState<FilesystemPriorityRoot[]>([]);
  const [status, setStatus] = useState<FilesystemIndexStatus | null>(null);
  const [policy, setPolicy] = useState<IndexingPolicy | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [policyDirty, setPolicyDirty] = useState(false);
  const [savingPolicy, setSavingPolicy] = useState(false);
  const [indexAction, setIndexAction] = useState<string | null>(null);
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
      const { priority_roots: _priorityRoots, paused: _paused, ...authoredPolicy } = indexSettings;
      setPolicy(authoredPolicy);
      setStatus(indexStatus);
      setDirty(false);
      setPolicyDirty(false);
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

  const savePolicy = useCallback(async () => {
    if (!policy) return;
    setSavingPolicy(true);
    setMessage(null);
    try {
      setStatus(await engine.setFilesystemIndexingSettings(policy));
      setPolicyDirty(false);
      setMessage({ text: "Local content and semantic indexing settings saved.", error: false });
    } catch (reason) {
      setMessage({ text: reason instanceof Error ? reason.message : String(reason), error: true });
    } finally {
      setSavingPolicy(false);
    }
  }, [policy]);

  const controlIndex = useCallback(async (action: "pause" | "resume" | "rebuild" | "clear") => {
    if (action === "rebuild" && !window.confirm("Rebuild the local filesystem index from scratch? Direct file browsing will keep working.")) return;
    if (action === "clear" && !window.confirm("Clear the local filesystem index and pause background indexing? Downloaded model files are not removed.")) return;
    setIndexAction(action);
    setMessage(null);
    try {
      setStatus(await engine.controlFilesystemIndex(action));
      setMessage({
        text: action === "clear"
          ? "Local index data cleared. Background indexing is paused; direct browsing still works."
          : action === "rebuild"
            ? "Local index cleared and rebuild started."
            : action === "pause"
              ? "Background indexing paused after the current folder."
              : "Background indexing resumed.",
        error: false,
      });
    } catch (reason) {
      setMessage({ text: reason instanceof Error ? reason.message : String(reason), error: true });
    } finally {
      setIndexAction(null);
    }
  }, []);

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

            {policy && (
              <div className="space-y-3 rounded-md border p-3">
                <div>
                  <h3 className="text-sm font-medium">Local understanding</h3>
                  <p className="text-xs text-muted-foreground">Extracted text and embeddings stay on this device and are quota bounded.</p>
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  <label className="flex items-start justify-between gap-3 rounded-md bg-muted/20 p-3">
                    <span>
                      <span className="block text-sm font-medium">Content search</span>
                      <span className="block text-xs text-muted-foreground">Index text from supported files for private full-text search.</span>
                    </span>
                    <Switch checked={policy.content_enabled} onCheckedChange={(enabled) => { setPolicy((current) => current ? setContentPolicy(current, enabled) : current); setPolicyDirty(true); }} />
                  </label>
                  <label className="flex items-start justify-between gap-3 rounded-md bg-muted/20 p-3">
                    <span>
                      <span className="block text-sm font-medium">Semantic search</span>
                      <span className="block text-xs text-muted-foreground">
                        {status?.fastembed_available ? "Create local embeddings for meaning-based search." : "Requires the optional local FastEmbed capability."}
                      </span>
                    </span>
                    <Switch disabled={!policy.content_enabled || status?.fastembed_available === false} checked={policy.semantic_enabled} onCheckedChange={(enabled) => { setPolicy((current) => current ? { ...current, semantic_enabled: enabled } : current); setPolicyDirty(true); }} />
                  </label>
                </div>
                <div className="grid gap-3 md:grid-cols-3">
                  <label className="space-y-1 text-xs">
                    <span className="font-medium">Text storage limit (MiB)</span>
                    <Input type="number" min={16} max={20 * 1024} value={Math.round(policy.max_content_bytes / (1024 * 1024))} onChange={(event) => { const mib = Number(event.target.value); if (Number.isFinite(mib)) { setPolicy((current) => current ? { ...current, max_content_bytes: Math.round(mib * 1024 * 1024) } : current); setPolicyDirty(true); } }} />
                    <span className="text-muted-foreground">{status?.content_entries.toLocaleString() ?? "0"} files · {((status?.content_bytes ?? 0) / (1024 * 1024)).toFixed(1)} MiB used</span>
                  </label>
                  <label className="space-y-1 text-xs">
                    <span className="font-medium">Embedding file limit</span>
                    <Input type="number" min={100} max={50_000} step={100} value={policy.max_embedding_entries} onChange={(event) => { const value = Number(event.target.value); if (Number.isFinite(value)) { setPolicy((current) => current ? { ...current, max_embedding_entries: Math.round(value) } : current); setPolicyDirty(true); } }} />
                    <span className="text-muted-foreground">{status?.embedding_entries.toLocaleString() ?? "0"} files embedded</span>
                  </label>
                  <label className="space-y-1 text-xs">
                    <span className="font-medium">Embedding model</span>
                    <Input value={policy.embedding_model} disabled={!policy.semantic_enabled} onChange={(event) => { setPolicy((current) => current ? { ...current, embedding_model: event.target.value } : current); setPolicyDirty(true); }} />
                    <span className="text-muted-foreground">Model changes rebuild embeddings progressively.</span>
                  </label>
                </div>
                <div className="flex justify-end">
                  <Button type="button" size="sm" disabled={!policyDirty || savingPolicy} onClick={() => void savePolicy()}>
                    {savingPolicy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />} Save index settings
                  </Button>
                </div>
              </div>
            )}

            <div className="space-y-3 rounded-md border p-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h3 className="text-sm font-medium">Index maintenance</h3>
                  <p className="text-xs text-muted-foreground">Direct browsing remains available while background indexing is paused or rebuilding.</p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button type="button" variant="outline" size="sm" disabled={indexAction !== null} onClick={() => void controlIndex(status?.paused ? "resume" : "pause")}>
                    {indexAction === "pause" || indexAction === "resume" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : status?.paused ? <CirclePlay className="h-3.5 w-3.5" /> : <CirclePause className="h-3.5 w-3.5" />}
                    {status?.paused ? "Resume" : "Pause"}
                  </Button>
                  <Button type="button" variant="outline" size="sm" disabled={indexAction !== null} onClick={() => void controlIndex("rebuild")}>
                    {indexAction === "rebuild" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />} Rebuild
                  </Button>
                  <Button type="button" variant="outline" size="sm" className="text-destructive hover:text-destructive" disabled={indexAction !== null} onClick={() => void controlIndex("clear")}>
                    {indexAction === "clear" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Eraser className="h-3.5 w-3.5" />} Clear index
                  </Button>
                </div>
              </div>
              <div className="grid gap-2 text-xs text-muted-foreground sm:grid-cols-3">
                <div><span className="font-medium text-foreground">Local storage:</span> {formatBytes(status?.storage_bytes)}</div>
                <div><span className="font-medium text-foreground">Last folder scan:</span> {formatTimestamp(status?.last_scan_at)}</div>
                <div><span className="font-medium text-foreground">Last location refresh:</span> {formatTimestamp(status?.last_reconcile_at)}</div>
              </div>
              <p className="text-[11px] text-muted-foreground">Clearing removes derived metadata, extracted text, and embeddings. It does not delete user files or downloaded model artifacts.</p>
            </div>

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

function formatBytes(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
  return `${(value / (1024 * 1024 * 1024)).toFixed(2)} GiB`;
}

function formatTimestamp(value: number | null | undefined): string {
  return value ? new Date(value * 1000).toLocaleString() : "Never";
}
