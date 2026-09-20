import { isTauri, DesktopBridgeUnavailableError } from "@/lib/sidecar";

/** Public selections and progress only. File paths, tokens and key data are
 * deliberately absent; native code owns chooser, verification and transfer. */
export type NativeVaultExchangeRequest =
  | { action: "begin_export"; item_ids: string[]; organization_id: string }
  | { action: "status" | "cancel"; operation_id: string };

export interface NativeVaultExchangeStatus {
  operation_id: string;
  phase: "idle" | "authorizing" | "preflighting" | "choosing_destination"
    | "exporting" | "handed_to_destination" | "cancelled" | "failed" | "unavailable";
  total: number;
  eligible: number;
  unsupported: number;
  handed_off: number;
  message: string;
}

export async function nativeVaultExchange(request: NativeVaultExchangeRequest): Promise<NativeVaultExchangeStatus> {
  if (!isTauri()) throw new DesktopBridgeUnavailableError();
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<NativeVaultExchangeStatus>("native_vault_exchange", { request });
}
