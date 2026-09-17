/**
 * What a session BUILT: the per-row number, and the manifest behind it.
 *
 * Split out of the coding-sessions page (audit 2026-09-14, UI-05). Same shape
 * as the conversation diagnosis: the engine's record, never a client-side
 * guess; a 404 from the lane is shown as the lane's own words.
 */

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Loader2, RefreshCw, ShieldCheck } from "lucide-react";

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
      title={`${summary.files.toLocaleString()} file${summary.files === 1 ? "" : "s"} kept (${formatFileSize(summary.bytes)}) · ${summary.uploaded.toLocaleString()} confirmed in AI Matrx${summary.deduplicated > 0 ? ` (${summary.deduplicated.toLocaleString()} of them share an identical file already there — AI Matrx holds ${summary.cloud_rows.toLocaleString()} file${summary.cloud_rows === 1 ? "" : "s"} for this session)` : ""}${summary.awaiting_confirmation > 0 ? ` · ${summary.awaiting_confirmation.toLocaleString()} not read back yet` : ""} · ${summary.pending_upload.toLocaleString()} pending${summary.missing_in_cloud > 0 ? ` · ${summary.missing_in_cloud.toLocaleString()} missing in AI Matrx (re-uploading)` : ""}${summary.failed_upload > 0 ? ` · ${summary.failed_upload.toLocaleString()} failed` : ""}. Click to see every file.`}
      className="underline decoration-dotted underline-offset-4 hover:decoration-solid"
      onClick={(event) => {
        event.stopPropagation();
        onOpen();
      }}
    >
      {summary.files.toLocaleString()}
      <span className="ml-1 text-xs text-muted-foreground">
        ({summary.uploaded.toLocaleString()}↑
        {summary.awaiting_confirmation > 0 ? ` ${summary.awaiting_confirmation.toLocaleString()} unconfirmed` : ""}
        {summary.pending_upload > 0 ? ` ${summary.pending_upload.toLocaleString()} pending` : ""}
        {summary.missing_in_cloud > 0 ? (
          <span className="text-destructive"> {summary.missing_in_cloud.toLocaleString()} missing</span>
        ) : null}
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
  const [verifying, setVerifying] = useState(false);
  const [verifyResult, setVerifyResult] = useState<string | null>(null);

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

  /** The repair the numbers imply: read the ids back, re-upload what is gone. */
  const verify = useCallback(async () => {
    setVerifying(true);
    setError(null);
    setVerifyResult(null);
    try {
      const result = await engine.verifyCodingSessionArtifacts();
      const refiling = result.queued_for_refiling ?? 0;
      const parts = [`${result.confirmed.toLocaleString()} confirmed`];
      if (result.missing_in_cloud > 0) {
        parts.push(
          `${result.missing_in_cloud.toLocaleString()} were missing from AI Matrx${(result.still_missing_in_cloud ?? 0) > 0 ? ` (${result.still_missing_in_cloud.toLocaleString()} still to go)` : ""}`,
        );
      }
      if (refiling > 0) {
        parts.push(
          `${refiling.toLocaleString()} were filed under another path and are being re-filed under this session's own${(result.still_unplaced ?? 0) > 0 ? ` (${result.still_unplaced.toLocaleString()} still to go)` : ""}`,
        );
      }
      if (result.missing_in_cloud > 0 || refiling > 0) {
        parts.push(
          `${(result.re_uploaded ?? 0).toLocaleString()} re-uploaded from the durable copy`,
        );
      }
      setVerifyResult(
        result.missing_in_cloud > 0 || refiling > 0
          ? `${parts.join(" · ")}.`
          : `${result.confirmed.toLocaleString()} file id${result.confirmed === 1 ? "" : "s"} read back from AI Matrx; every path has its own file there.`,
      );
      await load();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : String(nextError));
    } finally {
      setVerifying(false);
    }
  }, [load]);

  useEffect(() => {
    setDetail(null);
    setVerifyResult(null);
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
              {detail.files.toLocaleString()} file{detail.files === 1 ? "" : "s"} kept ·{" "}
              {formatFileSize(detail.bytes)} · {detail.uploaded.toLocaleString()} confirmed in AI
              Matrx{" "}
              {`(${detail.cloud_rows.toLocaleString()} file${detail.cloud_rows === 1 ? "" : "s"} there`}
              {detail.deduplicated > 0
                ? `, ${detail.deduplicated.toLocaleString()} of these paths share an identical file`
                : ""}
              {")"}
              {detail.unplaced > 0
                ? ` · ${detail.unplaced.toLocaleString()} not listed under this session yet (being re-filed under their own path)`
                : ""}
              {detail.placement_failed > 0
                ? ` · ${detail.placement_failed.toLocaleString()} AI Matrx would not file under their own path`
                : ""}
              {detail.superseded_versions > 0
                ? ` · ${detail.superseded_versions.toLocaleString()} earlier version${detail.superseded_versions === 1 ? "" : "s"}`
                : ""}
              {detail.awaiting_confirmation > 0
                ? ` · ${detail.awaiting_confirmation.toLocaleString()} not read back yet`
                : ""}
              {` · ${detail.pending_upload.toLocaleString()} pending`}
              {detail.missing_in_cloud > 0
                ? ` · ${detail.missing_in_cloud.toLocaleString()} missing in AI Matrx (re-uploading)`
                : ""}
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
            <Button
              variant="outline"
              size="sm"
              onClick={() => void verify()}
              disabled={verifying || loading}
              title="Read every recorded file id back from AI Matrx, re-upload anything it no longer serves, and re-file any path filed under another file."
            >
              {verifying ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <ShieldCheck className="mr-2 h-4 w-4" />
              )}
              Check AI Matrx
            </Button>
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

        {verifyResult && !error && (
          <div className="rounded-md border bg-muted/40 p-3 text-sm" role="status">
            {verifyResult}
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
                              <Badge variant="outline">
                                {entry.verified_at ? "In AI Matrx" : "Sent, not read back yet"}
                              </Badge>
                              {entry.deduplicated && (
                                <Badge
                                  variant="destructive"
                                  className="ml-1"
                                  title={`AI Matrx filed these bytes${entry.cloud_file_path ? ` at ${entry.cloud_file_path}` : " under another path"}, so this session's own path is not recorded there and the Artifacts panel cannot list it here. The lane re-files it under its own path.`}
                                >
                                  Not listed under this session
                                </Badge>
                              )}
                              <div className="mt-1 text-muted-foreground">
                                {whenLocal(entry.uploaded_at)} · file{" "}
                                <span className="font-mono">{entry.file_id}</span>
                              </div>
                              {entry.placement_error && (
                                <div className="mt-1 max-w-96 break-words text-destructive">
                                  {entry.placement_error}
                                </div>
                              )}
                            </>
                          ) : (
                            <>
                              <Badge
                                variant={
                                  entry.upload_error || entry.verify_error ? "destructive" : "outline"
                                }
                              >
                                {entry.verify_error
                                  ? "Missing in AI Matrx — re-uploading"
                                  : entry.upload_error
                                    ? "Upload failed"
                                    : "Pending"}
                              </Badge>
                              <div className="mt-1 text-muted-foreground">
                                {entry.upload_attempts} attempt{entry.upload_attempts === 1 ? "" : "s"}
                                {" · the durable copy on this Mac is intact"}
                              </div>
                              {entry.verify_error && (
                                <div className="mt-1 max-w-96 break-words text-destructive">
                                  {entry.verify_error}
                                </div>
                              )}
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
