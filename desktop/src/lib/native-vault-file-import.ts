import { isTauri, DesktopBridgeUnavailableError } from "@/lib/sidecar";

export type NativeVaultFileImportRequest =
  | { action: "begin_file_import"; organization_id: string }
  | { action: "recover" }
  | { action: "status" | "cancel"; operation_id: string }
  | { action: "preview"; operation_id: string; offset: number }
  | { action: "choose_scope"; operation_id: string; organization_id: string }
  | { action: "confirm"; operation_id: string; preview_digest: string };

export type NativeVaultImportPhase = "authorizing" | "preview" | "awaiting_confirmation" | "importing" | "partial" | "completed" | "cancelled" | "failed" | "unavailable";
export type NativeVaultImportDisposition = "eligible" | "unsupported" | "committed" | "failed" | "uncertain" | "not_attempted";
export interface NativeVaultFileImportStatus { operation_id: string; phase: NativeVaultImportPhase; total: number; committed: number; already_present: number; unsupported: number; failed: number; uncertain: number; not_attempted: number; message: string; }
export interface NativeVaultFileImportPreview { operation_id: string; preview_digest: string; offset: number; total: number; slots: Array<{ slot_id: string; title: string | null; disposition: NativeVaultImportDisposition; reason: string | null }>; }
export async function nativeVaultFileImport(request: NativeVaultFileImportRequest): Promise<NativeVaultFileImportStatus | NativeVaultFileImportPreview> {
  if (!isTauri()) throw new DesktopBridgeUnavailableError();
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke("native_vault_file_import", { request });
}
