import { useState } from "react";
import {
  Share2,
  Globe,
  Lock,
  Copy,
  Check,
  Trash2,
  UserPlus,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { engine } from "@/lib/api";
import type { DocShare } from "@/lib/api";

interface ShareDialogProps {
  noteId: string | null;
  folderId: string | null;
  userId: string;
  shares: DocShare[];
  onClose: () => void;
  onUpdate: () => void;
}

const PERMISSIONS = [
  { value: "read", label: "View only" },
  { value: "comment", label: "Can comment" },
  { value: "edit", label: "Can edit" },
  { value: "admin", label: "Admin" },
];

export function ShareDialog({
  noteId,
  folderId,
  userId,
  shares,
  onClose,
  onUpdate,
}: ShareDialogProps) {
  const [email, setEmail] = useState("");
  const [permission, setPermission] = useState("read");
  const [isPublic, setIsPublic] = useState(false);
  const [copied, setCopied] = useState(false);
  const [loading, setLoading] = useState(false);
  const [shareError, setShareError] = useState<string | null>(null);

  // Filter shares relevant to this note/folder
  const relevantShares = shares.filter((s) =>
    noteId ? s.note_id === noteId : s.folder_id === folderId,
  );
  const publicShare = relevantShares.find((s) => s.is_public);

  const handleShare = async () => {
    if (!email.trim()) return;
    setLoading(true);
    setShareError(null);
    try {
      await engine.createShare(userId, {
        note_id: noteId ?? undefined,
        folder_id: folderId ?? undefined,
        shared_with_id: email.trim(), // In production, resolve email to user_id
        permission,
      });
      setEmail("");
      onUpdate();
    } catch (err) {
      console.error("Share failed:", err);
      setShareError(
        `Failed to share: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setLoading(false);
    }
  };

  const handleTogglePublic = async () => {
    setLoading(true);
    setShareError(null);
    try {
      if (publicShare) {
        await engine.deleteShare(publicShare.id, userId);
      } else {
        await engine.createShare(userId, {
          note_id: noteId ?? undefined,
          folder_id: folderId ?? undefined,
          is_public: true,
          permission: "read",
        });
      }
      setIsPublic(!isPublic);
      onUpdate();
    } catch (err) {
      console.error("Toggle public failed:", err);
      setShareError(
        `Failed to update public access: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setLoading(false);
    }
  };

  const handleCopyLink = () => {
    if (publicShare?.public_token) {
      navigator.clipboard.writeText(
        `https://aimatrx.com/shared/${publicShare.public_token}`,
      );
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const handleDeleteShare = async (shareId: string) => {
    setShareError(null);
    try {
      await engine.deleteShare(shareId, userId);
      onUpdate();
    } catch (err) {
      console.error("Delete share failed:", err);
      setShareError(
        `Failed to remove share: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  };

  const handleUpdatePermission = async (
    shareId: string,
    newPermission: string,
  ) => {
    setShareError(null);
    try {
      await engine.updateShare(shareId, userId, { permission: newPermission });
      onUpdate();
    } catch (err) {
      console.error("Update permission failed:", err);
      setShareError(
        `Failed to update permission: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="w-full max-w-md rounded-lg border bg-background p-4 shadow-lg">
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Share2 className="h-4 w-4" />
            <h3 className="font-semibold">Share</h3>
          </div>
          <button
            onClick={onClose}
            className="rounded-md p-1 hover:bg-accent"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Error */}
        {shareError && (
          <div
            role="alert"
            className="mb-4 flex items-start gap-2 rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive"
          >
            <span className="flex-1">{shareError}</span>
            <button
              onClick={() => setShareError(null)}
              className="shrink-0 text-destructive/70 underline hover:text-destructive"
            >
              Dismiss
            </button>
          </div>
        )}

        {/* Add person */}
        <div className="flex items-center gap-2 mb-4">
          <input
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="User ID or email..."
            className="flex-1 rounded-md border bg-background px-3 py-1.5 text-sm outline-none focus:ring-1 focus:ring-primary"
            onKeyDown={(e) => e.key === "Enter" && handleShare()}
          />
          <select
            value={permission}
            onChange={(e) => setPermission(e.target.value)}
            className="rounded-md border bg-background px-2 py-1.5 text-sm outline-none"
          >
            {PERMISSIONS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
          <button
            onClick={handleShare}
            disabled={loading || !email.trim()}
            className="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            <UserPlus className="h-4 w-4" />
          </button>
        </div>

        {/* Current shares */}
        {relevantShares.filter((s) => !s.is_public).length > 0 && (
          <div className="mb-4">
            <h4 className="text-xs font-medium text-muted-foreground mb-2">
              Shared with
            </h4>
            <div className="flex flex-col gap-1">
              {relevantShares
                .filter((s) => !s.is_public)
                .map((s) => (
                  <div
                    key={s.id}
                    className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-accent"
                  >
                    <span className="flex-1 truncate">
                      {s.shared_with_id ?? "Unknown"}
                    </span>
                    <select
                      value={s.permission}
                      onChange={(e) =>
                        handleUpdatePermission(s.id, e.target.value)
                      }
                      className="rounded border bg-background px-1.5 py-0.5 text-xs"
                    >
                      {PERMISSIONS.map((p) => (
                        <option key={p.value} value={p.value}>
                          {p.label}
                        </option>
                      ))}
                    </select>
                    <button
                      onClick={() => handleDeleteShare(s.id)}
                      className="text-muted-foreground hover:text-destructive"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ))}
            </div>
          </div>
        )}

        {/* Public link */}
        <div className="border-t pt-3">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2 text-sm">
              {publicShare ? (
                <Globe className="h-4 w-4 text-blue-400" />
              ) : (
                <Lock className="h-4 w-4 text-muted-foreground" />
              )}
              <span>
                {publicShare
                  ? "Anyone with the link can view"
                  : "Only shared people can access"}
              </span>
            </div>
            <button
              onClick={handleTogglePublic}
              disabled={loading}
              className={cn(
                "rounded-md px-2 py-1 text-xs transition-colors",
                publicShare
                  ? "bg-blue-500/15 text-blue-400"
                  : "bg-accent text-muted-foreground hover:text-foreground",
              )}
            >
              {publicShare ? "Disable" : "Enable"}
            </button>
          </div>

          {publicShare?.public_token && (
            <div className="flex items-center gap-2">
              <input
                readOnly
                value={`https://aimatrx.com/shared/${publicShare.public_token}`}
                className="flex-1 rounded-md border bg-muted px-2 py-1 text-xs"
              />
              <button
                onClick={handleCopyLink}
                className="rounded-md px-2 py-1 text-xs hover:bg-accent"
              >
                {copied ? (
                  <Check className="h-3.5 w-3.5 text-emerald-500" />
                ) : (
                  <Copy className="h-3.5 w-3.5" />
                )}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
