import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, CheckCircle2, FileUp, Loader2, RefreshCw, XCircle } from "lucide-react";
import { Button } from "@ai-matrx/design-system";
import {
  nativeVaultFileImport,
  type NativeVaultFileImportPreview,
  type NativeVaultFileImportStatus,
  type NativeVaultImportDisposition,
} from "@/lib/native-vault-file-import";
import { subscribeNativeVaultHostEvents } from "@/lib/native-vault-auth";
import {
  ACTIVE_ORGANIZATION_CHANGE_EVENT,
  getActiveOrganizationId,
  requireActiveOrganizationId,
} from "@/lib/org/active-org";

type ScreenState =
  | { kind: "idle"; message: string | null }
  | { kind: "status"; status: NativeVaultFileImportStatus; message: string | null }
  | { kind: "preview"; status: NativeVaultFileImportStatus; preview: NativeVaultFileImportPreview; message: string | null };

const PROGRESS_POLL_DELAY_MS = 1_000;
const MAX_AUTOMATIC_PROGRESS_POLLS = 30;
const ACTIVE_PHASES = new Set<NativeVaultFileImportStatus["phase"]>(["authorizing", "importing"]);
const TERMINAL_PHASES = new Set<NativeVaultFileImportStatus["phase"]>(["completed", "partial", "cancelled", "failed", "unavailable"]);

function isPreview(value: NativeVaultFileImportStatus | NativeVaultFileImportPreview): value is NativeVaultFileImportPreview {
  return "preview_digest" in value;
}

export function nativeVaultImportDispositionLabel(disposition: NativeVaultImportDisposition): string {
  switch (disposition) {
    case "eligible": return "Ready to import";
    case "unsupported": return "Cannot import";
    case "committed": return "Imported";
    case "failed": return "Could not import";
    case "uncertain": return "Needs recovery";
    case "not_attempted": return "Not imported";
  }
}

function terminalMessage(status: NativeVaultFileImportStatus): string {
  if (status.phase === "completed") return `Imported ${status.committed} passkey${status.committed === 1 ? "" : "s"}.`;
  if (status.phase === "partial") return `${status.committed} imported; ${status.unsupported + status.failed + status.uncertain + status.not_attempted} need attention.`;
  if (status.phase === "cancelled") return "Import cancelled. Nothing else will be imported.";
  if (status.phase === "unavailable") return "Passkey import is unavailable on this Mac.";
  if (status.phase === "failed") return "The import did not finish. Recover it before trying a new file.";
  return status.message;
}

function statusTone(phase: NativeVaultFileImportStatus["phase"]): string {
  if (phase === "completed") return "border-emerald-500/40 bg-emerald-500/5 text-emerald-700 dark:text-emerald-400";
  if (phase === "partial" || phase === "failed" || phase === "unavailable") return "border-amber-500/40 bg-amber-500/5 text-amber-800 dark:text-amber-300";
  if (phase === "cancelled") return "border-border bg-muted/40 text-muted-foreground";
  return "border-primary/30 bg-primary/5 text-foreground";
}

/**
 * Value-free native passkey import controls. The host owns the file chooser,
 * parsing and key custody; this component only receives accounting metadata.
 */
export function NativeVaultImport() {
  const [screen, setScreen] = useState<ScreenState>({ kind: "idle", message: null });
  const [busy, setBusy] = useState(false);
  const requestGeneration = useRef(0);
  const mounted = useRef(false);
  const operation = useRef<{ id: string; organizationId: string | null } | null>(null);
  const lastStatus = useRef<NativeVaultFileImportStatus | null>(null);
  const operationPending = useRef(false);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollAttempts = useRef(0);
  const refreshRef = useRef<((automatic: boolean) => Promise<void>) | undefined>(undefined);
  // Covers the small interval after the user chooses an organization but
  // before the native chooser returns an operation id.
  const selectedOrganization = useRef<string | null>(null);

  const stopPolling = useCallback(() => {
    if (pollTimer.current !== null) clearTimeout(pollTimer.current);
    pollTimer.current = null;
    pollAttempts.current = 0;
  }, []);

  const invalidate = useCallback((message: string) => {
    requestGeneration.current += 1;
    const active = operation.current;
    operation.current = null;
    lastStatus.current = null;
    operationPending.current = false;
    selectedOrganization.current = null;
    stopPolling();
    setBusy(false);
    setScreen({ kind: "idle", message });
    if (active) void nativeVaultFileImport({ action: "cancel", operation_id: active.id }).catch(() => undefined);
  }, [stopPolling]);

  const present = useCallback((generation: number, next: ScreenState) => {
    if (mounted.current && generation === requestGeneration.current) setScreen(next);
  }, []);

  const isCurrent = useCallback((generation: number) => mounted.current && generation === requestGeneration.current, []);

  const preserveActiveOperation = useCallback((generation: number, message: string) => {
    const status = lastStatus.current;
    if (operation.current && status) present(generation, { kind: "status", status, message });
    else present(generation, { kind: "idle", message });
  }, [present]);

  const loadFullPreview = useCallback(async (operationId: string, generation: number): Promise<NativeVaultFileImportPreview | null> => {
    let offset = 0;
    let full: NativeVaultFileImportPreview | null = null;
    const slotIds = new Set<string>();
    while (true) {
      if (!isCurrent(generation)) return null;
      const page = await nativeVaultFileImport({ action: "preview", operation_id: operationId, offset });
      if (!isCurrent(generation)) return null;
      if (!isPreview(page)) throw new Error("The native importer did not return the file preview.");
      if (page.offset !== offset) throw new Error("The native importer returned an out-of-order file preview.");
      if (!full) full = { ...page, slots: [...page.slots] };
      else if (page.operation_id !== full.operation_id || page.preview_digest !== full.preview_digest || page.total !== full.total) throw new Error("The native importer returned an inconsistent file preview.");
      else full.slots.push(...page.slots);
      for (const slot of page.slots) {
        if (slotIds.has(slot.slot_id)) throw new Error("The native importer returned a duplicate passkey preview item.");
        slotIds.add(slot.slot_id);
      }
      if (full.slots.length === full.total) return full;
      if (full.slots.length > full.total) throw new Error("The native importer returned too many passkey preview items.");
      if (page.slots.length === 0) throw new Error("The native importer returned an incomplete file preview.");
      offset += page.slots.length;
    }
  }, [isCurrent]);

  const schedulePoll = useCallback((operationId: string) => {
    if (pollTimer.current !== null || !operation.current || operation.current.id !== operationId) return;
    if (pollAttempts.current >= MAX_AUTOMATIC_PROGRESS_POLLS) {
      setScreen((current) => current.kind === "idle" ? current : { ...current, message: "This import is still running. Refresh progress to continue checking it." });
      return;
    }
    pollAttempts.current += 1;
    pollTimer.current = setTimeout(() => {
      pollTimer.current = null;
      if (operation.current?.id === operationId) void refreshRef.current?.(true);
    }, PROGRESS_POLL_DELAY_MS);
  }, []);

  const begin = useCallback(async () => {
    const generation = ++requestGeneration.current;
    stopPolling();
    operationPending.current = true;
    setBusy(true);
    setScreen({ kind: "idle", message: null });
    try {
      const organizationId = await requireActiveOrganizationId();
      if (!isCurrent(generation)) return;
      selectedOrganization.current = organizationId;
      const result = await nativeVaultFileImport({ action: "begin_file_import", organization_id: organizationId });
      if (isPreview(result)) throw new Error("The native importer returned a preview before an operation was opened.");
      if (!isCurrent(generation)) {
        void nativeVaultFileImport({ action: "cancel", operation_id: result.operation_id }).catch(() => undefined);
        return;
      }
      operation.current = { id: result.operation_id, organizationId };
      lastStatus.current = result;
      if (result.phase === "preview" || result.phase === "awaiting_confirmation") {
        const preview = await loadFullPreview(result.operation_id, generation);
        if (!preview) return;
        present(generation, { kind: "preview", status: result, preview, message: null });
      } else {
        present(generation, { kind: "status", status: result, message: null });
        if (ACTIVE_PHASES.has(result.phase)) schedulePoll(result.operation_id);
      }
    } catch (error) {
      if (mounted.current && generation === requestGeneration.current) {
        const message = error instanceof Error ? error.message : "Could not open the native passkey importer.";
        if (operation.current) preserveActiveOperation(generation, message);
        else {
          operationPending.current = false;
          setScreen({ kind: "idle", message });
        }
      }
    } finally {
      if (mounted.current && generation === requestGeneration.current) setBusy(false);
    }
  }, [isCurrent, loadFullPreview, present, preserveActiveOperation, schedulePoll, stopPolling]);

  const refresh = useCallback(async (automatic = false) => {
    const active = operation.current;
    if (!active) return;
    if (!automatic) stopPolling();
    const generation = ++requestGeneration.current;
    if (!automatic) setBusy(true);
    try {
      const result = await nativeVaultFileImport({ action: "status", operation_id: active.id });
      if (!isCurrent(generation)) return;
      if (isPreview(result)) throw new Error("The native importer returned an invalid progress update.");
      lastStatus.current = result;
      if ((result.phase === "preview" || result.phase === "awaiting_confirmation") && active.organizationId) {
        const preview = await loadFullPreview(active.id, generation);
        if (!preview) return;
        present(generation, { kind: "preview", status: result, preview, message: null });
      } else {
        present(generation, { kind: "status", status: result, message: active.organizationId ? null : "Recovery found an interrupted import. Its original destination remains private to the native worker; choose the file again before confirming anything." });
      }
      if (["completed", "partial", "cancelled", "failed", "unavailable"].includes(result.phase)) {
        operation.current = null;
        lastStatus.current = null;
        operationPending.current = false;
        selectedOrganization.current = null;
        stopPolling();
      } else if (ACTIVE_PHASES.has(result.phase)) {
        schedulePoll(active.id);
      }
    } catch (error) {
      preserveActiveOperation(generation, error instanceof Error ? error.message : "Could not refresh import progress.");
    } finally {
      if (!automatic && mounted.current && generation === requestGeneration.current) setBusy(false);
    }
  }, [isCurrent, loadFullPreview, present, preserveActiveOperation, schedulePoll, stopPolling]);

  refreshRef.current = refresh;

  const confirmImport = useCallback(async () => {
    if (screen.kind !== "preview") return;
    const active = operation.current;
    if (!active || !active.organizationId || active.id !== screen.preview.operation_id) return;
    const generation = ++requestGeneration.current;
    stopPolling();
    setBusy(true);
    try {
      // A preview is never authority to write. Re-read the user-selected org
      // immediately before both native scope binding and confirmation.
      const currentOrganizationId = await getActiveOrganizationId();
      if (!isCurrent(generation)) return;
      if (currentOrganizationId !== active.organizationId) {
        invalidate("Your selected organization changed. Choose the file again before importing.");
        return;
      }
      const scoped = await nativeVaultFileImport({ action: "choose_scope", operation_id: active.id, organization_id: currentOrganizationId });
      if (!isCurrent(generation)) return;
      if (isPreview(scoped)) throw new Error("The native importer returned an invalid destination update.");
      const result = await nativeVaultFileImport({ action: "confirm", operation_id: active.id, preview_digest: screen.preview.preview_digest });
      if (!isCurrent(generation)) return;
      if (isPreview(result)) throw new Error("The native importer returned an invalid import result.");
      lastStatus.current = result;
      present(generation, { kind: "status", status: result, message: terminalMessage(result) });
      if (["completed", "partial", "cancelled", "failed", "unavailable"].includes(result.phase)) {
        operation.current = null;
        lastStatus.current = null;
        operationPending.current = false;
        selectedOrganization.current = null;
        stopPolling();
      } else if (ACTIVE_PHASES.has(result.phase)) {
        schedulePoll(active.id);
      }
    } catch (error) {
      preserveActiveOperation(generation, error instanceof Error ? error.message : "Could not confirm the passkey import.");
    } finally {
      if (mounted.current && generation === requestGeneration.current) setBusy(false);
    }
  }, [invalidate, isCurrent, present, preserveActiveOperation, schedulePoll, stopPolling, screen]);

  const cancel = useCallback(async () => {
    const active = operation.current;
    if (!active) return;
    const generation = ++requestGeneration.current;
    setBusy(true);
    try {
      const result = await nativeVaultFileImport({ action: "cancel", operation_id: active.id });
      if (!isCurrent(generation)) return;
      if (isPreview(result)) throw new Error("The native importer returned an invalid cancellation result.");
      lastStatus.current = result;
      present(generation, { kind: "status", status: result, message: terminalMessage(result) });
      if (TERMINAL_PHASES.has(result.phase)) {
        operation.current = null;
        lastStatus.current = null;
        operationPending.current = false;
        selectedOrganization.current = null;
        stopPolling();
      } else if (ACTIVE_PHASES.has(result.phase)) {
        schedulePoll(active.id);
      }
    } catch (error) {
      preserveActiveOperation(generation, error instanceof Error ? error.message : "Could not cancel the passkey import.");
    } finally {
      if (mounted.current && generation === requestGeneration.current) setBusy(false);
    }
  }, [isCurrent, present, preserveActiveOperation, schedulePoll, stopPolling]);

  const recover = useCallback(async () => {
    const generation = ++requestGeneration.current;
    stopPolling();
    operationPending.current = true;
    setBusy(true);
    try {
      const result = await nativeVaultFileImport({ action: "recover" });
      if (!isCurrent(generation)) return;
      if (isPreview(result)) throw new Error("The native importer returned an invalid recovery status.");
      lastStatus.current = result;
      operation.current = ["authorizing", "preview", "awaiting_confirmation", "importing"].includes(result.phase)
        ? { id: result.operation_id, organizationId: null }
        : null;
      operationPending.current = operation.current !== null;
      selectedOrganization.current = operation.current?.organizationId ?? null;
      present(generation, { kind: "status", status: result, message: operation.current ? "Recovery found an interrupted import. Its original destination remains private to the native worker; choose the file again before confirming anything." : terminalMessage(result) });
      if (operation.current && ACTIVE_PHASES.has(result.phase)) schedulePoll(result.operation_id);
    } catch (error) {
      preserveActiveOperation(generation, error instanceof Error ? error.message : "Could not recover an interrupted import.");
    } finally {
      if (mounted.current && generation === requestGeneration.current) {
        if (!operation.current) operationPending.current = false;
        setBusy(false);
      }
    }
  }, [isCurrent, present, preserveActiveOperation, schedulePoll, stopPolling]);

  useEffect(() => {
    mounted.current = true;
    const unsubscribe = subscribeNativeVaultHostEvents(() => {
      if (operationPending.current || operation.current || selectedOrganization.current) invalidate("Your account changed. This import was stopped locally; start it again under the current account.");
    });
    const onOrganizationChange = () => {
      if (operationPending.current || operation.current || selectedOrganization.current) invalidate("Your selected organization changed. This import was stopped locally; start it again for the new destination.");
    };
    window.addEventListener(ACTIVE_ORGANIZATION_CHANGE_EVENT, onOrganizationChange);
    return () => {
      mounted.current = false;
      requestGeneration.current += 1;
      stopPolling();
      unsubscribe();
      window.removeEventListener(ACTIVE_ORGANIZATION_CHANGE_EVENT, onOrganizationChange);
    };
  }, [invalidate, stopPolling]);

  const status = screen.kind === "idle" ? null : screen.status;
  const preview = screen.kind === "preview" ? screen.preview : null;
  const accounted = status ? status.committed + status.already_present + status.unsupported + status.failed + status.uncertain + status.not_attempted : 0;
  const percent = status && status.total > 0 ? Math.round((accounted / status.total) * 100) : 0;
  const canConfirm = preview?.slots.some((slot) => slot.disposition === "eligible") === true;

  return <section aria-labelledby="native-passkey-import-title" className="rounded-lg border border-border bg-card p-4">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h3 id="native-passkey-import-title" className="flex items-center gap-2 text-sm font-semibold"><FileUp className="h-4 w-4 text-primary" />Import passkeys from a file</h3>
        <p className="mt-1 max-w-2xl text-xs text-muted-foreground">Choose a supported passkey exchange file, review every item, then explicitly import the compatible passkeys into the organization selected on this device.</p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={() => void begin()} disabled={busy || operation.current !== null}><FileUp className="h-3.5 w-3.5" />Choose file</Button>
        <Button size="sm" variant="outline" onClick={() => void recover()} disabled={busy || operation.current !== null}><RefreshCw className="h-3.5 w-3.5" />Recover import</Button>
      </div>
    </div>

    {screen.message && <div role="status" className="mt-3 flex gap-2 rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-xs text-amber-800 dark:text-amber-300"><AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />{screen.message}</div>}
    {busy && <div className="mt-3 flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />Working with the native importer…</div>}

    {status && <div className={`mt-3 rounded-md border px-3 py-3 text-xs ${statusTone(status.phase)}`}>
      <div className="flex flex-wrap items-center justify-between gap-2"><p className="font-medium">{screen.message ?? terminalMessage(status)}</p><span>{accounted} of {status.total} accounted for</span></div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-black/10 dark:bg-white/15"><div className="h-full rounded-full bg-current transition-all" style={{ width: `${percent}%` }} /></div>
      <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3"><span>Imported: {status.committed}</span><span>Already present: {status.already_present}</span><span>Unsupported: {status.unsupported}</span><span>Failed: {status.failed}</span><span>Needs recovery: {status.uncertain}</span><span>Not attempted: {status.not_attempted}</span></div>
      <p className="mt-2 text-muted-foreground">{status.message}</p>
      {operation.current && <div className="mt-3 flex flex-wrap gap-2"><Button size="sm" variant="outline" onClick={() => void refresh()} disabled={busy}>Refresh progress</Button><Button size="sm" variant="outline" onClick={() => void cancel()} disabled={busy}><XCircle className="h-3.5 w-3.5" />Cancel import</Button></div>}
    </div>}

    {preview && <div className="mt-3 rounded-md border border-border p-3 text-xs">
      <p className="font-medium">Review before importing</p>
      <p className="mt-1 text-muted-foreground">Only the {preview.slots.filter((slot) => slot.disposition === "eligible").length} compatible passkey{preview.slots.filter((slot) => slot.disposition === "eligible").length === 1 ? "" : "s"} in this complete file preview will be imported. Import goes to the organization currently selected on this device.</p>
      <ul className="mt-3 space-y-2" aria-label="Passkey file preview">{preview.slots.map((slot) => <li key={slot.slot_id} className="flex items-start justify-between gap-3 rounded border border-border/70 px-2.5 py-2"><span className="min-w-0 truncate font-medium">{slot.title ?? "Unnamed passkey"}</span><span className={slot.disposition === "eligible" ? "shrink-0 text-emerald-700 dark:text-emerald-400" : "shrink-0 text-amber-800 dark:text-amber-300"}>{nativeVaultImportDispositionLabel(slot.disposition)}{slot.reason ? `: ${slot.reason}` : ""}</span></li>)}</ul>
      <div className="mt-3 flex flex-wrap gap-2"><Button size="sm" onClick={() => void confirmImport()} disabled={busy || !canConfirm}><CheckCircle2 className="h-3.5 w-3.5" />Confirm import</Button><Button size="sm" variant="outline" onClick={() => void cancel()} disabled={busy}>Cancel</Button></div>
    </div>}
  </section>;
}
