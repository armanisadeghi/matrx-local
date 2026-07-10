/**
 * PrivateVaultPanel — the Private vault UI (iPhone-hidden-folder style).
 *
 * Three states driven by vault status:
 *  (a) No vault  → creation flow (explanation, password+confirm, strength
 *      hint, show/hide, explicit "I understand" checkbox).
 *  (b) Locked    → unlock form with wrong-password error + auto-lock note.
 *  (c) Unlocked  → grid of decrypted vault items (blob thumbs, same card
 *      style as the library), select mode with Restore and two-step
 *      permanent Delete, Lock-now button, item count, and a footer
 *      change-password form.
 *
 * The vault hook instance is OWNED BY THE PARENT (MediaLibrarySection) and
 * passed in as props — there must be exactly one instance so the move flow
 * and this panel share state.
 *
 * All operations are loud on failure; restore shows per-item results.
 */

import { useState, useEffect, useCallback, useRef } from "react";
import {
  AlertCircle,
  Check,
  CheckSquare,
  Eye,
  EyeOff,
  Film,
  Image as ImageIcon,
  KeyRound,
  Loader2,
  Lock,
  LockOpen,
  ShieldCheck,
  Square,
  Trash2,
  Undo2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { MediaLibraryItem, MediaVaultOpResult } from "@/lib/api";
import type {
  MediaVaultState,
  MediaVaultActions,
} from "@/hooks/use-media-vault";
import { ErrorNote } from "./shared";

// ── Password helpers ─────────────────────────────────────────────────────────

const MIN_PASSWORD_LENGTH = 8;

function passwordStrength(pw: string): {
  label: string;
  tone: "weak" | "ok" | "strong";
} {
  if (pw.length < MIN_PASSWORD_LENGTH) return { label: "Too short", tone: "weak" };
  let variety = 0;
  if (/[a-z]/.test(pw)) variety++;
  if (/[A-Z]/.test(pw)) variety++;
  if (/[0-9]/.test(pw)) variety++;
  if (/[^a-zA-Z0-9]/.test(pw)) variety++;
  if (pw.length >= 12 && variety >= 3) return { label: "Strong", tone: "strong" };
  if (pw.length >= 10 && variety >= 2) return { label: "Good", tone: "ok" };
  return { label: "Weak — consider a longer mix of characters", tone: "weak" };
}

const STRENGTH_COLOR: Record<"weak" | "ok" | "strong", string> = {
  weak: "text-amber-600 dark:text-amber-400",
  ok: "text-blue-600 dark:text-blue-400",
  strong: "text-green-600 dark:text-green-400",
};

/** Password input with a show/hide toggle. */
function PasswordField({
  id,
  value,
  onChange,
  placeholder,
  autoFocus,
  disabled,
  onEnter,
}: {
  id: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  autoFocus?: boolean;
  disabled?: boolean;
  onEnter?: () => void;
}) {
  const [show, setShow] = useState(false);
  return (
    <div className="relative">
      <Input
        id={id}
        type={show ? "text" : "password"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        autoFocus={autoFocus}
        disabled={disabled}
        autoComplete="off"
        className="pr-9"
        onKeyDown={(e) => {
          if (e.key === "Enter" && onEnter) onEnter();
        }}
      />
      <button
        type="button"
        tabIndex={-1}
        onClick={() => setShow((s) => !s)}
        className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
        aria-label={show ? "Hide password" : "Show password"}
      >
        {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
      </button>
    </div>
  );
}

// ── Reusable unlock form (also used by the library's move flow) ──────────────

export function VaultUnlockForm({
  actions,
  busy,
  autoLockSeconds,
  onUnlocked,
}: {
  actions: MediaVaultActions;
  busy: boolean;
  autoLockSeconds: number | null;
  onUnlocked?: () => void;
}) {
  const [password, setPassword] = useState("");
  const [unlockError, setUnlockError] = useState<string | null>(null);

  const handleUnlock = useCallback(async () => {
    if (!password) return;
    setUnlockError(null);
    const err = await actions.unlock(password);
    if (err) {
      setUnlockError(err);
      return;
    }
    setPassword("");
    onUnlocked?.();
  }, [password, actions, onUnlocked]);

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <Label htmlFor="vault-unlock-password" className="text-xs">
          Vault password
        </Label>
        <PasswordField
          id="vault-unlock-password"
          value={password}
          onChange={(v) => {
            setPassword(v);
            setUnlockError(null);
          }}
          placeholder="Enter your vault password"
          autoFocus
          disabled={busy}
          onEnter={() => void handleUnlock()}
        />
      </div>
      {unlockError && <ErrorNote message={unlockError} />}
      <Button
        size="sm"
        className="w-full"
        disabled={busy || !password}
        onClick={() => void handleUnlock()}
      >
        {busy ? (
          <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
        ) : (
          <LockOpen className="h-3.5 w-3.5 mr-1.5" />
        )}
        Unlock
      </Button>
      {autoLockSeconds !== null && autoLockSeconds > 0 && (
        <p className="text-[11px] text-muted-foreground text-center">
          The vault locks itself automatically after{" "}
          {Math.round(autoLockSeconds / 60)} minute
          {Math.round(autoLockSeconds / 60) === 1 ? "" : "s"} of inactivity.
        </p>
      )}
    </div>
  );
}

// ── Creation flow ────────────────────────────────────────────────────────────

function VaultCreateFlow({
  actions,
  busy,
}: {
  actions: MediaVaultActions;
  busy: boolean;
}) {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [understood, setUnderstood] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const strength = password ? passwordStrength(password) : null;
  const mismatch = confirm.length > 0 && confirm !== password;
  const canCreate =
    password.length >= MIN_PASSWORD_LENGTH &&
    confirm === password &&
    understood &&
    !busy;

  const handleCreate = useCallback(async () => {
    if (!canCreate) return;
    setCreateError(null);
    const err = await actions.create(password);
    if (err) setCreateError(err);
    // On success the parent re-renders into the unlocked state.
  }, [canCreate, actions, password]);

  return (
    <div className="space-y-4 max-w-md mx-auto">
      <div className="flex flex-col items-center gap-2 text-center pt-2">
        <div className="rounded-full bg-violet-500/10 p-3">
          <ShieldCheck className="h-7 w-7 text-violet-500" />
        </div>
        <h3 className="text-sm font-semibold">Create your Private vault</h3>
        <p className="text-xs text-muted-foreground leading-relaxed">
          Media you move to Private is encrypted on disk and protected by your
          password. Matrx retains a sealed recovery key so your content can be
          recovered if you lose the password.
        </p>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="vault-create-password" className="text-xs">
          Password (min {MIN_PASSWORD_LENGTH} characters)
        </Label>
        <PasswordField
          id="vault-create-password"
          value={password}
          onChange={setPassword}
          placeholder="Choose a password"
          autoFocus
          disabled={busy}
        />
        {strength && (
          <p className={`text-[11px] ${STRENGTH_COLOR[strength.tone]}`}>
            {strength.label}
          </p>
        )}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="vault-create-confirm" className="text-xs">
          Confirm password
        </Label>
        <PasswordField
          id="vault-create-confirm"
          value={confirm}
          onChange={setConfirm}
          placeholder="Repeat the password"
          disabled={busy}
          onEnter={() => void handleCreate()}
        />
        {mismatch && (
          <p className="text-[11px] text-destructive">Passwords do not match</p>
        )}
      </div>

      <label className="flex items-start gap-2 text-xs cursor-pointer select-none">
        <input
          type="checkbox"
          checked={understood}
          onChange={(e) => setUnderstood(e.target.checked)}
          disabled={busy}
          className="mt-0.5 accent-violet-600"
        />
        <span>
          I understand that my Private items are protected by this password.
        </span>
      </label>

      {createError && <ErrorNote message={createError} />}

      <Button
        size="sm"
        className="w-full"
        disabled={!canCreate}
        onClick={() => void handleCreate()}
      >
        {busy ? (
          <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
        ) : (
          <Lock className="h-3.5 w-3.5 mr-1.5" />
        )}
        Create vault
      </Button>
    </div>
  );
}

// ── Vault media preview (decrypted blob URL loader) ──────────────────────────

function VaultMediaPreview({
  item,
  fileUrls,
  getFileUrl,
  className,
}: {
  item: MediaLibraryItem;
  fileUrls: Record<string, string>;
  getFileUrl: MediaVaultActions["getFileUrl"];
  className?: string;
}) {
  const url = fileUrls[item.id] ?? null;
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    if (!url) {
      void getFileUrl(item.id).then((result) => {
        if (!cancelled && result === null) setFailed(true);
      });
    }
    return () => {
      cancelled = true;
    };
  }, [item.id, url, getFileUrl]);

  if (failed) {
    return (
      <div
        className={`flex items-center justify-center bg-muted/30 text-muted-foreground ${className ?? ""}`}
      >
        <AlertCircle className="h-5 w-5 opacity-40" />
      </div>
    );
  }
  if (!url) {
    return (
      <div
        className={`flex items-center justify-center bg-muted/30 text-muted-foreground ${className ?? ""}`}
      >
        <Loader2 className="h-5 w-5 animate-spin opacity-40" />
      </div>
    );
  }
  if (item.media_type === "video") {
    return (
      <video
        src={url}
        className={className}
        muted
        loop
        playsInline
        onMouseEnter={(e) => void e.currentTarget.play().catch(() => undefined)}
        onMouseLeave={(e) => e.currentTarget.pause()}
      />
    );
  }
  return (
    <img src={url} alt={item.prompt.slice(0, 80)} className={className} />
  );
}

// ── Per-item batch results (loud partial failures) ──────────────────────────

function BatchResultsNote({
  title,
  results,
  onDismiss,
}: {
  title: string;
  results: MediaVaultOpResult[];
  onDismiss: () => void;
}) {
  const failures = results.filter((r) => !r.ok);
  if (failures.length === 0) return null;
  return (
    <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive space-y-1">
      <div className="flex items-center gap-2">
        <AlertCircle className="h-3.5 w-3.5 shrink-0" />
        <span className="font-medium flex-1">
          {title}: {failures.length} of {results.length} item
          {results.length === 1 ? "" : "s"} failed
        </span>
        <button
          onClick={onDismiss}
          className="text-destructive/70 hover:text-destructive"
        >
          Dismiss
        </button>
      </div>
      <ul className="list-disc pl-5 space-y-0.5">
        {failures.map((f) => (
          <li key={f.item_id} className="break-all">
            {f.item_id}: {f.error ?? "unknown error"}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ── Change-password footer form ──────────────────────────────────────────────

function ChangePasswordForm({
  actions,
  busy,
}: {
  actions: MediaVaultActions;
  busy: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const strength = next ? passwordStrength(next) : null;
  const mismatch = confirm.length > 0 && confirm !== next;
  const canSubmit =
    current.length > 0 &&
    next.length >= MIN_PASSWORD_LENGTH &&
    confirm === next &&
    !busy;

  const handleSubmit = useCallback(async () => {
    if (!canSubmit) return;
    setError(null);
    setDone(false);
    const err = await actions.changePassword(current, next);
    if (err) {
      setError(err);
      return;
    }
    setCurrent("");
    setNext("");
    setConfirm("");
    setDone(true);
  }, [canSubmit, actions, current, next]);

  if (!open) {
    return (
      <div className="flex items-center justify-between border-t pt-3">
        <span className="text-[11px] text-muted-foreground">
          Vault settings
        </span>
        <Button
          size="sm"
          variant="ghost"
          className="h-7 text-xs"
          onClick={() => {
            setOpen(true);
            setDone(false);
          }}
        >
          <KeyRound className="h-3.5 w-3.5 mr-1.5" />
          Change password
        </Button>
      </div>
    );
  }

  return (
    <div className="border-t pt-3 space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium flex items-center gap-1.5">
          <KeyRound className="h-3.5 w-3.5" />
          Change password
        </span>
        <Button
          size="sm"
          variant="ghost"
          className="h-7 text-xs"
          onClick={() => {
            setOpen(false);
            setError(null);
          }}
        >
          Cancel
        </Button>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="space-y-1">
          <Label htmlFor="vault-pw-current" className="text-[11px]">
            Current password
          </Label>
          <PasswordField
            id="vault-pw-current"
            value={current}
            onChange={setCurrent}
            disabled={busy}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="vault-pw-next" className="text-[11px]">
            New password
          </Label>
          <PasswordField
            id="vault-pw-next"
            value={next}
            onChange={setNext}
            disabled={busy}
          />
          {strength && (
            <p className={`text-[10px] ${STRENGTH_COLOR[strength.tone]}`}>
              {strength.label}
            </p>
          )}
        </div>
        <div className="space-y-1">
          <Label htmlFor="vault-pw-confirm" className="text-[11px]">
            Confirm new password
          </Label>
          <PasswordField
            id="vault-pw-confirm"
            value={confirm}
            onChange={setConfirm}
            disabled={busy}
            onEnter={() => void handleSubmit()}
          />
          {mismatch && (
            <p className="text-[10px] text-destructive">
              Passwords do not match
            </p>
          )}
        </div>
      </div>
      {error && <ErrorNote message={error} onDismiss={() => setError(null)} />}
      {done && (
        <p className="text-xs text-green-600 dark:text-green-400 flex items-center gap-1.5">
          <Check className="h-3.5 w-3.5" />
          Password changed
        </p>
      )}
      <Button
        size="sm"
        disabled={!canSubmit}
        onClick={() => void handleSubmit()}
      >
        {busy ? (
          <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
        ) : (
          <KeyRound className="h-3.5 w-3.5 mr-1.5" />
        )}
        Update password
      </Button>
    </div>
  );
}

// ── Unlocked vault grid ──────────────────────────────────────────────────────

function VaultGrid({
  vault,
  actions,
}: {
  vault: MediaVaultState;
  actions: MediaVaultActions;
}) {
  const { items, itemsLoading, busy, fileUrls } = vault;
  const [selecting, setSelecting] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [restoring, setRestoring] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [batchResults, setBatchResults] = useState<{
    title: string;
    results: MediaVaultOpResult[];
  } | null>(null);
  const [successNote, setSuccessNote] = useState<string | null>(null);
  const lastClickedIndex = useRef<number | null>(null);

  const exitSelection = useCallback(() => {
    setSelecting(false);
    setSelectedIds(new Set());
    setConfirmingDelete(false);
    lastClickedIndex.current = null;
  }, []);

  const toggleSelect = useCallback(
    (item: MediaLibraryItem, index: number, shiftKey: boolean) => {
      setSelectedIds((prev) => {
        const next = new Set(prev);
        if (
          shiftKey &&
          lastClickedIndex.current !== null &&
          lastClickedIndex.current !== index
        ) {
          const [lo, hi] = [
            Math.min(lastClickedIndex.current, index),
            Math.max(lastClickedIndex.current, index),
          ];
          for (let i = lo; i <= hi; i++) next.add(items[i].id);
        } else if (next.has(item.id)) {
          next.delete(item.id);
        } else {
          next.add(item.id);
        }
        return next;
      });
      lastClickedIndex.current = index;
      setConfirmingDelete(false);
    },
    [items],
  );

  const handleRestore = useCallback(async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    setRestoring(true);
    setBatchResults(null);
    setSuccessNote(null);
    const results = await actions.restore(ids);
    setRestoring(false);
    if (results === null) return; // hook surfaced the error (or flipped to locked)
    const okCount = results.filter((r) => r.ok).length;
    if (okCount > 0) {
      setSuccessNote(
        `${okCount} item${okCount === 1 ? "" : "s"} restored to the library`,
      );
    }
    setBatchResults({ title: "Restore", results });
    exitSelection();
  }, [selectedIds, actions, exitSelection]);

  const handleDelete = useCallback(async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    if (!confirmingDelete) {
      setConfirmingDelete(true);
      return;
    }
    setDeleting(true);
    setBatchResults(null);
    setSuccessNote(null);
    const results: MediaVaultOpResult[] = [];
    for (const id of ids) {
      const ok = await actions.deleteItem(id);
      results.push(
        ok ? { item_id: id, ok } : { item_id: id, ok, error: "delete failed" },
      );
    }
    setDeleting(false);
    const okCount = results.filter((r) => r.ok).length;
    if (okCount > 0) {
      setSuccessNote(
        `${okCount} item${okCount === 1 ? "" : "s"} permanently deleted`,
      );
    }
    setBatchResults({ title: "Delete", results });
    exitSelection();
  }, [selectedIds, confirmingDelete, actions, exitSelection]);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs text-muted-foreground flex items-center gap-1.5">
          <LockOpen className="h-3.5 w-3.5 text-green-600 dark:text-green-400" />
          Unlocked · {items.length} item{items.length === 1 ? "" : "s"}
        </span>
        <div className="flex-1" />
        {items.length > 0 && (
          <Button
            size="sm"
            variant={selecting ? "secondary" : "outline"}
            className="h-7 text-xs"
            onClick={() => (selecting ? exitSelection() : setSelecting(true))}
          >
            <CheckSquare className="h-3.5 w-3.5 mr-1.5" />
            {selecting ? "Cancel" : "Select"}
          </Button>
        )}
        <Button
          size="sm"
          variant="default"
          className="h-7 text-xs"
          disabled={busy}
          onClick={() => void actions.lock()}
        >
          <Lock className="h-3.5 w-3.5 mr-1.5" />
          Lock now
        </Button>
      </div>

      {selecting && (
        <div className="flex items-center gap-2 flex-wrap rounded-lg border bg-muted/20 px-3 py-2">
          <span className="text-xs font-medium">
            {selectedIds.size} selected
          </span>
          <Button
            size="sm"
            variant="ghost"
            className="h-7 text-xs"
            onClick={() =>
              setSelectedIds(new Set(items.map((i) => i.id)))
            }
          >
            Select all
          </Button>
          <div className="flex-1" />
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-xs"
            disabled={selectedIds.size === 0 || restoring || deleting}
            onClick={() => void handleRestore()}
          >
            {restoring ? (
              <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
            ) : (
              <Undo2 className="h-3.5 w-3.5 mr-1.5" />
            )}
            Restore to library
          </Button>
          <Button
            size="sm"
            variant={confirmingDelete ? "destructive" : "outline"}
            className="h-7 text-xs"
            disabled={selectedIds.size === 0 || restoring || deleting}
            onClick={() => void handleDelete()}
          >
            {deleting ? (
              <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
            ) : (
              <Trash2 className="h-3.5 w-3.5 mr-1.5" />
            )}
            {confirmingDelete ? "Confirm permanent delete" : "Delete forever"}
          </Button>
        </div>
      )}

      {successNote && (
        <p className="text-xs text-green-600 dark:text-green-400 flex items-center gap-1.5">
          <Check className="h-3.5 w-3.5" />
          {successNote}
        </p>
      )}
      {batchResults && (
        <BatchResultsNote
          title={batchResults.title}
          results={batchResults.results}
          onDismiss={() => setBatchResults(null)}
        />
      )}

      {itemsLoading && items.length === 0 ? (
        <div className="flex items-center justify-center py-16 gap-3 text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />
          <span className="text-sm">Loading vault…</span>
        </div>
      ) : items.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed py-16 gap-3 text-muted-foreground">
          <Lock className="h-10 w-10 opacity-20" />
          <span className="text-sm">Your vault is empty</span>
          <span className="text-xs text-center max-w-xs">
            Select items in the Library and choose "Move to Private" to store
            them here, encrypted.
          </span>
        </div>
      ) : (
        <div className="grid gap-3 grid-cols-2 sm:grid-cols-3 lg:grid-cols-4">
          {items.map((item, index) => {
            const isSelected = selectedIds.has(item.id);
            return (
              <button
                key={item.id}
                onClick={(e) => {
                  if (selecting) toggleSelect(item, index, e.shiftKey);
                }}
                className={`group relative rounded-lg border bg-card text-left overflow-hidden transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 ${
                  isSelected
                    ? "border-violet-500 ring-1 ring-violet-500"
                    : "hover:border-violet-500/40"
                } ${selecting ? "cursor-pointer" : "cursor-default"}`}
              >
                <div className="relative aspect-square w-full overflow-hidden bg-muted/20">
                  <VaultMediaPreview
                    item={item}
                    fileUrls={fileUrls}
                    getFileUrl={actions.getFileUrl}
                    className="h-full w-full object-cover"
                  />
                  <span className="absolute left-1.5 top-1.5 rounded bg-black/60 px-1.5 py-0.5 text-[10px] text-white flex items-center gap-1">
                    {item.media_type === "video" ? (
                      <Film className="h-2.5 w-2.5" />
                    ) : (
                      <ImageIcon className="h-2.5 w-2.5" />
                    )}
                    {item.width}×{item.height}
                  </span>
                  {selecting && (
                    <span
                      className={`absolute right-1.5 top-1.5 rounded p-0.5 ${
                        isSelected
                          ? "bg-violet-600 text-white"
                          : "bg-black/50 text-white/80"
                      }`}
                    >
                      {isSelected ? (
                        <CheckSquare className="h-4 w-4" />
                      ) : (
                        <Square className="h-4 w-4" />
                      )}
                    </span>
                  )}
                </div>
                <div className="p-2.5">
                  <p className="text-xs font-medium truncate" title={item.prompt}>
                    {item.prompt || "(no prompt)"}
                  </p>
                </div>
              </button>
            );
          })}
        </div>
      )}

      <ChangePasswordForm actions={actions} busy={busy} />
    </div>
  );
}

// ── Panel (state switch) ─────────────────────────────────────────────────────

export function PrivateVaultPanel({
  vault,
  actions,
}: {
  vault: MediaVaultState;
  actions: MediaVaultActions;
}) {
  const { status, statusLoading, busy, error } = vault;

  if (statusLoading && !status) {
    return (
      <div className="flex items-center justify-center py-16 gap-3 text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
        <span className="text-sm">Checking vault…</span>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {error && <ErrorNote message={error} onDismiss={actions.clearError} />}

      {!status ? (
        // Status fetch failed outright — the error note above says why.
        <div className="flex flex-col items-center justify-center py-12 gap-2 text-muted-foreground">
          <AlertCircle className="h-8 w-8 opacity-30" />
          <span className="text-sm">Vault status unavailable</span>
          <Button
            size="sm"
            variant="outline"
            onClick={() => void actions.refresh()}
          >
            Retry
          </Button>
        </div>
      ) : !status.exists ? (
        <VaultCreateFlow actions={actions} busy={busy} />
      ) : !status.unlocked ? (
        <div className="max-w-sm mx-auto space-y-4 pt-4">
          <div className="flex flex-col items-center gap-2 text-center">
            <div className="rounded-full bg-muted p-3">
              <Lock className="h-7 w-7 text-muted-foreground" />
            </div>
            <h3 className="text-sm font-semibold">Vault locked</h3>
            {status.item_count !== null && (
              <p className="text-xs text-muted-foreground">
                {status.item_count} item{status.item_count === 1 ? "" : "s"}{" "}
                inside
              </p>
            )}
          </div>
          <VaultUnlockForm
            actions={actions}
            busy={busy}
            autoLockSeconds={status.auto_lock_seconds}
          />
        </div>
      ) : (
        <VaultGrid vault={vault} actions={actions} />
      )}
    </div>
  );
}
