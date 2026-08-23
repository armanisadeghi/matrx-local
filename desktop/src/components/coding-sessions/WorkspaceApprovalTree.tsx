import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  ExternalLink,
  Folder,
  FolderCheck,
  FolderGit2,
  FolderPlus,
  Loader2,
  RefreshCw,
  Trash2,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { invoke } from "@tauri-apps/api/core";
import { engine } from "@/lib/api";
import type {
  WorkspaceDiscoveryNode,
  WorkspaceDiscoveryResult,
} from "@/lib/api";

type WorkspaceApprovalTreeProps = {
  workspaceRoots: string[];
  approvedFolders: string[];
  disabled?: boolean;
  onChanged: () => Promise<void>;
  onError: (message: string) => void;
};

export function isWithin(parent: string, child: string): boolean {
  const normalizedParent = parent.replace(/[\\/]+$/, "");
  return (
    child === normalizedParent ||
    child.startsWith(`${normalizedParent}/`) ||
    child.startsWith(`${normalizedParent}\\`)
  );
}

export function hasProject(node: WorkspaceDiscoveryNode): boolean {
  return node.kind !== "directory" || node.children.some(hasProject);
}

export function filterWorkspaceNode(node: WorkspaceDiscoveryNode, query: string): WorkspaceDiscoveryNode | null {
  const normalized = query.trim().toLocaleLowerCase();
  if (!normalized) return node;
  const children = node.children.map((child) => filterWorkspaceNode(child, normalized)).filter((child): child is WorkspaceDiscoveryNode => child !== null);
  const matches = node.name.toLocaleLowerCase().includes(normalized) || node.path.toLocaleLowerCase().includes(normalized) || node.project_kinds.some((kind) => kind.toLocaleLowerCase().includes(normalized));
  return matches || children.length > 0 ? { ...node, children } : null;
}

function projectLabel(node: WorkspaceDiscoveryNode): string {
  if (node.kind === "git_repository") return "Git repository";
  if (node.project_kinds.length === 0) return "Project";
  return node.project_kinds.join(" · ");
}

function WorkspaceNode({
  node,
  approvedFolders,
  busyPath,
  onApprove,
  onRevoke,
  onCopy,
  onOpen,
  depth = 0,
}: {
  node: WorkspaceDiscoveryNode;
  approvedFolders: string[];
  busyPath: string | null;
  onApprove: (path: string) => Promise<void>;
  onRevoke: (path: string) => Promise<void>;
  onCopy: (path: string) => void;
  onOpen: (path: string) => Promise<void>;
  depth?: number;
}) {
  const visibleChildren = useMemo(
    () => node.children.filter(hasProject),
    [node.children],
  );
  const [expanded, setExpanded] = useState(depth < 2);
  const exactApproval = approvedFolders.includes(node.path);
  const inheritedApproval = approvedFolders.some(
    (folder) => folder !== node.path && isWithin(folder, node.path),
  );
  const inheritedFrom = approvedFolders.find((folder) => folder !== node.path && isWithin(folder, node.path));
  const isProject = node.kind !== "directory";
  const hasChildren = visibleChildren.length > 0;

  if (!isProject && !hasChildren && depth > 0) return null;

  return (
    <li>
      <div
        className="group flex min-h-9 items-center gap-2 rounded-md px-2 py-1 hover:bg-muted/50"
        style={{ paddingLeft: `${Math.min(depth, 8) * 18 + 8}px` }}
      >
        {hasChildren ? (
          <button
            type="button"
            className="rounded p-0.5 hover:bg-muted"
            onClick={() => setExpanded((current) => !current)}
            aria-label={`${expanded ? "Collapse" : "Expand"} ${node.name}`}
            aria-expanded={expanded}
          >
            {expanded ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" />
            )}
          </button>
        ) : (
          <span className="w-[18px]" />
        )}
        {isProject ? (
          <FolderGit2 className="h-4 w-4 shrink-0 text-blue-600" />
        ) : (
          <Folder className="h-4 w-4 shrink-0 text-muted-foreground" />
        )}
        <span className="min-w-0 flex-1 truncate text-sm" title={node.path}>
          {node.name}
        </span>
        <Button type="button" variant="ghost" size="sm" className="h-7 w-7 p-0 opacity-70 hover:opacity-100" onClick={() => onCopy(node.path)} aria-label={`Copy path for ${node.name}`}><Copy className="h-3.5 w-3.5" /></Button>
        <Button type="button" variant="ghost" size="sm" className="h-7 w-7 p-0 opacity-70 hover:opacity-100" onClick={() => void onOpen(node.path)} aria-label={`Open ${node.name} in Finder`}><ExternalLink className="h-3.5 w-3.5" /></Button>
        {isProject && (
          <span className="hidden text-xs text-muted-foreground sm:inline">
            {projectLabel(node)}
          </span>
        )}
        {exactApproval ? (
          <>
            <Badge variant="secondary" className="gap-1">
              <Check className="h-3 w-3" /> Approved
            </Badge>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 px-2"
              onClick={() => void onRevoke(node.path)}
              disabled={busyPath !== null}
              aria-label={`Revoke ${node.name}`}
            >
              {busyPath === node.path ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Trash2 className="h-3.5 w-3.5" />
              )}
            </Button>
          </>
        ) : inheritedApproval ? (
          <Badge variant="outline" title={inheritedFrom}>Inherited approval</Badge>
        ) : isProject ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7"
            onClick={() => void onApprove(node.path)}
            disabled={busyPath !== null}
          >
            {busyPath === node.path ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <FolderCheck className="mr-1.5 h-3.5 w-3.5" />
            )}
            Approve project
          </Button>
        ) : null}
      </div>
      {expanded && hasChildren && (
        <ul>
          {visibleChildren.map((child) => (
            <WorkspaceNode
              key={child.path}
              node={child}
              approvedFolders={approvedFolders}
              busyPath={busyPath}
              onApprove={onApprove}
              onRevoke={onRevoke}
              onCopy={onCopy}
              onOpen={onOpen}
              depth={depth + 1}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

export function WorkspaceApprovalTree({
  workspaceRoots,
  approvedFolders,
  disabled = false,
  onChanged,
  onError,
}: WorkspaceApprovalTreeProps) {
  const [discovery, setDiscovery] = useState<WorkspaceDiscoveryResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [busyPath, setBusyPath] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [feedback, setFeedback] = useState<string | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);

  const reportError = useCallback((reason: unknown) => {
    const message = reason instanceof Error ? reason.message : String(reason);
    setLocalError(message);
    onError(message);
  }, [onError]);

  const discover = useCallback(async () => {
    setLoading(true);
    try {
      setDiscovery(await engine.discoverRuntimeWorkspaces());
    } catch (reason) {
      reportError(reason);
    } finally {
      setLoading(false);
    }
  }, [reportError]);

  useEffect(() => {
    void discover();
  }, [discover, workspaceRoots.join("\u0000")]);

  const chooseCodeLocation = async () => {
    let selected: string | string[] | null;
    try {
      const { open } = await import("@tauri-apps/plugin-dialog");
      selected = await open({ directory: true, multiple: false });
    } catch (reason) {
      reportError(
        reason instanceof Error
          ? reason.message
          : "The native folder picker is unavailable.",
      );
      return;
    }
    if (typeof selected !== "string") return;
    setBusyPath(selected);
    try {
      await engine.addRuntimeWorkspaceRoot(selected);
      await onChanged();
    } catch (reason) {
      reportError(reason);
    } finally {
      setBusyPath(null);
    }
  };

  const mutateApproval = async (path: string, approve: boolean) => {
    if (!approve && !window.confirm(`Revoke agent access to ${path}? Active runs are not stopped, but future runs will no longer be allowed to start there.`)) return;
    setBusyPath(path);
    try {
      if (approve) await engine.approveRuntimeFolder(path);
      else await engine.revokeRuntimeFolder(path);
      await onChanged();
      setDiscovery((current) =>
        current
          ? {
              ...current,
              approved_folders: approve
                ? [...new Set([...current.approved_folders, path])]
                : current.approved_folders.filter((item) => item !== path),
            }
          : current,
      );
    } catch (reason) {
      reportError(reason);
    } finally {
      setBusyPath(null);
    }
  };

  const removeRoot = async (path: string) => {
    if (!window.confirm(`Remove ${path} from code locations? Existing folder approvals beneath it may become unusable until the location is added again.`)) return;
    setBusyPath(path);
    try {
      const result = await engine.removeRuntimeWorkspaceRoot(path);
      await onChanged();
      if (result.affected_approvals?.length) {
        reportError(
          `${result.affected_approvals.length} existing approval${
            result.affected_approvals.length === 1 ? " is" : "s are"
          } now outside your code locations. Revoke ${
            result.affected_approvals.length === 1 ? "it" : "them"
          } or add the location again.`,
        );
      }
    } catch (reason) {
      reportError(reason);
    } finally {
      setBusyPath(null);
    }
  };

  const roots = discovery?.roots ?? [];
  const filteredRoots = useMemo(() => roots.map((root) => filterWorkspaceNode(root, query)).filter((root): root is WorkspaceDiscoveryNode => root !== null), [query, roots]);

  const copyPath = (path: string) => {
    void navigator.clipboard.writeText(path).then(() => setFeedback(`Copied ${path}`)).catch(reportError);
  };

  const openPath = async (path: string) => {
    try {
      await invoke("open_filesystem_path", { path, reveal: false });
      setFeedback(`Opened ${path}`);
    } catch (reason) {
      reportError(reason);
    }
  };

  return (
    <section aria-labelledby="code-locations-heading" className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 id="code-locations-heading" className="text-sm font-medium">
            Code locations and project access
          </h3>
          <p className="mt-1 max-w-3xl text-xs text-muted-foreground">
            Choose a parent such as your Code folder once. AI Matrx discovers
            repositories beneath it without reading project files. Then approve
            the whole location or only the projects an agent may work in.
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void discover()}
            disabled={disabled || loading || busyPath !== null}
          >
            {loading ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="mr-2 h-4 w-4" />
            )}
            Scan again
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={() => void chooseCodeLocation()}
            disabled={disabled || busyPath !== null}
          >
            <FolderPlus className="mr-2 h-4 w-4" />
            Add code location
          </Button>
        </div>
      </div>

      {workspaceRoots.length > 0 && <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter projects and paths" aria-label="Filter code locations and projects" />}
      {feedback && <div className="rounded-md border bg-muted/30 p-2 text-xs text-muted-foreground" role="status">{feedback}</div>}
      {localError && <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive" role="alert"><span>{localError}</span><Button type="button" variant="outline" size="sm" onClick={() => { setLocalError(null); void discover(); }}>Clear and scan again</Button></div>}

      {workspaceRoots.length === 0 && !loading && (
        <div className="rounded-md border border-dashed p-5 text-center">
          <FolderPlus className="mx-auto h-6 w-6 text-muted-foreground" />
          <p className="mt-2 text-sm font-medium">No code locations selected</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Use the folder picker to select a project or a parent containing
            many repositories. You never need to type a filesystem path.
          </p>
        </div>
      )}

      {workspaceRoots.filter((root) => !query || filteredRoots.some((candidate) => candidate.path === root)).map((root) => {
        const node = filteredRoots.find((candidate) => candidate.path === root);
        const exactApproval = approvedFolders.includes(root);
        return (
          <div key={root} className="rounded-md border">
            <div className="flex flex-wrap items-center gap-2 border-b bg-muted/20 px-3 py-2">
              <Folder className="h-4 w-4 text-blue-600" />
              <span className="min-w-0 flex-1 truncate font-mono text-xs" title={root}>
                {root}
              </span>
              <Button type="button" variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={() => copyPath(root)} aria-label={`Copy code location ${root}`}><Copy className="h-3.5 w-3.5" /></Button>
              <Button type="button" variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={() => void openPath(root)} aria-label={`Open code location ${root} in Finder`}><ExternalLink className="h-3.5 w-3.5" /></Button>
              {exactApproval ? (
                <>
                  <Badge variant="secondary">All projects approved</Badge>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-7"
                    onClick={() => void mutateApproval(root, false)}
                    disabled={busyPath !== null}
                  >
                    Revoke parent
                  </Button>
                </>
              ) : (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-7"
                  onClick={() => void mutateApproval(root, true)}
                  disabled={busyPath !== null}
                >
                  <FolderCheck className="mr-1.5 h-3.5 w-3.5" />
                  Approve all under this location
                </Button>
              )}
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 px-2"
                onClick={() => void removeRoot(root)}
                disabled={busyPath !== null}
                aria-label={`Remove code location ${root}`}
              >
                {busyPath === root ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Trash2 className="h-3.5 w-3.5" />
                )}
              </Button>
            </div>
            {node ? (
              <ul className="max-h-[420px] overflow-y-auto py-1">
                <WorkspaceNode
                  node={node}
                  approvedFolders={approvedFolders}
                  busyPath={busyPath}
                  onApprove={(path) => mutateApproval(path, true)}
                  onRevoke={(path) => mutateApproval(path, false)}
                  onCopy={copyPath}
                  onOpen={openPath}
                />
              </ul>
            ) : loading ? (
              <div className="flex items-center gap-2 p-3 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" /> Discovering projects…
              </div>
            ) : (
              <div className="p-3 text-sm text-muted-foreground">
                This location is unavailable or could not be scanned.
              </div>
            )}
          </div>
        );
      })}

      {query && filteredRoots.length === 0 && <div className="rounded-md border border-dashed p-4 text-center text-sm text-muted-foreground">No discovered projects or paths match “{query}”.</div>}

      {discovery && (
        <p className="text-xs text-muted-foreground" aria-live="polite">
          Found {discovery.project_count.toLocaleString()} projects across{" "}
          {discovery.directory_count.toLocaleString()} folders.
          {discovery.truncated
            ? " The safety scan limit was reached; choose a narrower location to see more."
            : ""}
          {discovery.skipped > 0
            ? ` ${discovery.skipped.toLocaleString()} hidden, generated, linked, or unreadable folders were skipped.`
            : ""}
        </p>
      )}
    </section>
  );
}
