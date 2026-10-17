import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  createApiVaultPort,
  createVaultKnobs,
  DEFAULT_LOCATION,
  locationFromParams,
  locationToParams,
  VaultApp,
  type VaultHostCapabilities,
  type VaultLocation,
} from "@ai-matrx/vault";
import { openExternalUrl } from "@/components/media-gen/shared";
import supabase from "@/lib/supabase";
import { getNativeVaultProviderStatus, isTauri, type NativeVaultProviderStatus } from "@/lib/sidecar";
import { useActiveOrganization } from "@/lib/org/use-active-organization";
import {
  desktopNativeImport,
  desktopVaultApi,
  desktopVaultIdentity,
  nativeProviderStatusOf,
} from "@/features/vault/host";

const VAULT_PATH = "/vault";

/** The address the workspace writes: everything `locationToParams` carries, plus the open item. */
function hrefOf(loc: VaultLocation): string {
  const q = locationToParams(loc);
  if (loc.itemId) q.set("item", loc.itemId);
  const qs = q.toString();
  return `${VAULT_PATH}${qs ? `?${qs}` : ""}`;
}

/**
 * The Vault (`@ai-matrx/vault`) over this app: logins, keys, secrets, one-time passwords, sharing,
 * import/export, and — only here — passkey import from a file and the macOS AutoFill provider.
 * The address (`/vault?…`) carries the workspace state, so Back and a pasted link reopen it.
 */
export function DesktopVault() {
  const location = useLocation();
  const navigate = useNavigate();
  const { organization, userId } = useActiveOrganization();

  // AppLayout keeps this page mounted while another page shows; keep the last Vault address then.
  const lastLocation = useRef<VaultLocation>(DEFAULT_LOCATION);
  if (location.pathname === VAULT_PATH) lastLocation.current = locationFromParams(new URLSearchParams(location.search));
  const vaultLocation = lastLocation.current;

  const [provider, setProvider] = useState<NativeVaultProviderStatus | null>(null);
  useEffect(() => {
    if (!isTauri()) return;
    let live = true;
    const read = () =>
      void getNativeVaultProviderStatus().then(
        (status) => live && setProvider(status),
        () => live && setProvider(null),
      );
    read();
    // The person turns AutoFill on in System Settings; coming back to the app re-reads it.
    window.addEventListener("focus", read);
    return () => {
      live = false;
      window.removeEventListener("focus", read);
    };
  }, []);

  const port = useMemo(() => createApiVaultPort(desktopVaultApi(), { userState: supabase, platformDoor: supabase.schema("platform") }), []);

  const organizationId = organization?.id ?? null;
  const host = useMemo<VaultHostCapabilities>(() => {
    const capabilities: VaultHostCapabilities = {
      identity: desktopVaultIdentity,
      openUrl: (url) => void openExternalUrl(url),
    };
    if (organizationId && userId) capabilities.knobs = createVaultKnobs(supabase, { organizationId, userId });
    if (isTauri()) {
      capabilities.nativeImport = desktopNativeImport;
      capabilities.nativeProvider = {
        status: nativeProviderStatusOf(provider),
        // Status, Enable and the macOS settings door live in Settings → Vault.
        openSetup: () => navigate("/settings?tab=vault"),
      };
    }
    return capabilities;
  }, [organizationId, userId, provider, navigate]);

  return (
    <div className="h-full w-full overflow-hidden">
      {/* A different account is a different vault: remount, never carry one person's state to the next. */}
      <VaultApp
        key={userId ?? "signed-out"}
        port={port}
        host={host}
        location={vaultLocation}
        onLocationChange={(next, how) => navigate(hrefOf(next), { replace: how === "replace" })}
      />
    </div>
  );
}
