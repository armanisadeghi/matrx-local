/**
 * What a session BUILT: the per-row number, and the manifest behind it.
 *
 * Split out of the coding-sessions page (audit 2026-09-14, UI-05). Same shape
 * as the conversation diagnosis: the engine's record, never a client-side
 * guess; a 404 from the lane is shown as the lane's own words.
 */

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Loader2, RefreshCw } from "lucide-react";

import { Badge, Button } from "@ai-matrx/design-system";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { RevealButton, whenLocal } from "@/components/coding-sessions/shared";
import { engine } from "@/lib/api";
import type {
  CodingSessionArtifactsSessionDetail,
  CodingSessionArtifactsSessionSummary,
} from "@/lib/api";
import { formatFileSize } from "@ai-matrx/kit/format";

/**
 * The per-row artifacts number: the lane's file count for this session, or
 * "—" when the lane holds nothing for it. Loading and endpoint failure are
 * each shown as themselves — never as a dash that looks like "none".
 */
export function ArtifactsCell({
  sessions,
  error,
  sessionId,
  onOpen,
}: {
  sessions: Map<string, CodingSessionArtifactsSessionSummary> | null;
  error: string | null;
  sessionId: string;
  onOpen: () => void;
}) {
  if (error) {
    return (
      <span className="text-destructive" title={`The artifacts lane could not be read: ${error}`}>
        ?
      </span>
    );
  }
  if (!sessions) {
    return <span className="text-muted-foreground" title="Asking the artifacts lane…">…</span>;
  }
  const summary = sessions.get(sessionId);
  if (!summary) {
    return (
      <span className="text-muted-foreground" title="The artifacts lane holds no files for this session.">
        —
      </span>
    );
  }
  return (
    <button
      type="button"
      title={`${summary.files.toLocaleString()} file${summary.files === 1 ? "" : "s"} kept (${formatFileSize(summary.bytes)}) · ${summary.uploaded.toLocaleString()} in AI Matrx · ${summary.pending_upload.toLocaleString()} pending${summary.failed_upload > 0 ? ` · ${summary.failed_upload.toLocaleString()} failed` : ""}. Click to see every file.`}
      className="underline decoration-dotted underline-offset-4 hover:decoration-solid"
      onClick={(event) => {
        event.stopPropagation();
        onOpen();
      }}
    >
      {summary.files.toLocaleString()}
      <span className="ml-1 text-xs text-muted-foreground">
        ({summary.uploaded.toLocaleString()}↑
        {summary.pending_upload > 0 ? ` ${summary.pending_upload.toLocaleString()} pending` : ""}
        {summary.failed_upload > 0 ? (
          <span className="text-destructive"> {summary.failed_upload.toLocaleString()} failed</span>
        ) : null}
        )
      </span>
    </button>
  );
}

/**
 * Every artifact the lane holds for one session — the manifest, row by row.
 * Same shape as the conversation diagnosis: the engine's record, never a
 * client-side guess; a 404 from the lane is shown as the lane's own words.
 */
export function SessionArtifactsDialog({
  sessionId,
  onClose,
}: {
  sessionId: string | null;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<CodingSessionArtifactsSessionDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    setError(null);
    try {
      setDetail(await engine.getCodingSessionArtifactsSession(sessionId));
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : String(nextError));
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    setDetail(null);
    void load();
  }, [load]);

  const entries = detail
    ? Object.entries(detail.entries).sort(([a], [b]) => a.localeCompare(b))
    : [];

  return (
    <Dialog open={Boolean(sessionId)} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="max-h-[88vh] max-w-5xl overflow-hidden">
        <DialogHeader>
          <DialogTitle>Artifacts this session built</DialogTitle>
          <DialogDescription>
            Every file the artifacts lane kept for session {sessionId}, and whether AI Matrx holds it.
          </DialogDescription>
        </DialogHeader>

        <div className="flex items-center justify-between gap-3">
          {detail ? (
            <span className="text-sm">
              {detail.files.toLocaleString()} file{detail.files === 1 ? "" : "s"} ·{" "}
              {formatFileSize(detail.bytes)} · {detail.uploaded.toLocaleString()} in AI Matrx ·{" "}
              {detail.pending_upload.toLocaleString()} pending
              {detail.failed_upload > 0 ? ` · ${detail.failed_upload.toLocaleString()} failed` : ""}
              {detail.abandoned_upload > 0 ? ` · ${detail.abandoned_upload.toLocaleString()} abandoned` : ""}
              {detail.skipped_over_size > 0 ? ` · ${detail.skipped_over_size.toLocaleString()} skipped (over size)` : ""}
              {detail.skipped_over_count > 0 ? ` · ${detail.skipped_over_count.toLocaleString()} skipped (over count)` : ""}
            </span>
          ) : (
            <span className="text-sm text-muted-foreground">{error ? "Not available" : "Loading…"}</span>
          )}
          <div className="flex items-center gap-2">
            {detail && <RevealButton path={detail.durable_dir} />}
            <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
              {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
              Refresh
            </Button>
          </div>
        </div>

        {error && (
          <div className="flex gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive" role="alert">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            {error}
          </div>
        )}

        {detail && (
          <div className="flex max-h-[68vh] flex-col gap-3 overflow-y-auto pr-1">
            <div className="grid grid-cols-[11rem_1fr] gap-3 rounded-lg border px-3 py-2 text-sm">
              <div className="text-muted-foreground">Project</div>
              <div className="min-w-0 break-words">{detail.project_slug}</div>
              <div className="text-muted-foreground">Scratchpad</div>
              <div className="min-w-0 break-all font-mono text-xs">{detail.scratchpad}</div>
              <div className="text-muted-foreground">Durable folder</div>
              <div className="min-w-0 break-all font-mono text-xs">{detail.durable_dir}</div>
            </div>
            {entries.length === 0 ? (
              <p className="px-1 text-sm text-muted-foreground">
                The lane holds no files for this session.
              </p>
            ) : (
              <div className="overflow-auto rounded-md border">
                <table className="w-full min-w-[820px] text-xs">
                  <thead className="bg-muted/40 text-left">
                    <tr>
                      <th className="px-2 py-1">File</th>
                      <th className="px-2 py-1 text-right">Size</th>
                      <th className="px-2 py-1">Captured</th>
                      <th className="px-2 py-1">AI Matrx</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {entries.map(([relativePath, entry]) => (
                      <tr key={relativePath}>
                        <td className="px-2 py-1.5 align-top">
                          <div className="break-all font-mono">{relativePath}</div>
                          <div className="text-muted-foreground" title={entry.sha256}>
                            sha256 {entry.sha256.slice(0, 12)}
                          </div>
                        </td>
                        <td className="px-2 py-1.5 text-right align-top tabular-nums">
                          {formatFileSize(entry.size)}
                        </td>
                        <td className="px-2 py-1.5 align-top whitespace-nowrap">
                          {whenLocal(entry.captured_at)}
                        </td>
                        <td className="px-2 py-1.5 align-top">
                          {entry.file_id ? (
                            <>
                              <Badge variant="outline">Uploaded</Badge>
                              <div className="mt-1 text-muted-foreground">
                                {whenLocal(entry.uploaded_at)} · file{" "}
                                <span className="font-mono">{entry.file_id}</span>
                              </div>
                            </>
                          ) : (
                            <>
                              <Badge variant={entry.upload_error ? "destructive" : "outline"}>
                                {entry.upload_error ? "Upload failed" : "Pending"}
                              </Badge>
                              <div className="mt-1 text-muted-foreground">
                                {entry.upload_attempts} attempt{entry.upload_attempts === 1 ? "" : "s"}
                              </div>
                              {entry.upload_error && (
                                <div className="mt-1 max-w-96 break-words text-destructive">
                                  {entry.upload_error}
                                </div>
                              )}
                            </>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
