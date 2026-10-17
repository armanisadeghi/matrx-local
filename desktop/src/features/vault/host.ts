/**
 * Matrx Local's host for `@ai-matrx/vault`: the Vault workspace over this app's own identity.
 *
 * - `api`: the generated client at the remote-config aidream address, carrying the custodian's
 *   short-lived token and the window's organization (`active-org.ts`, the only store).
 * - `userState` / `platformDoor`: the ONE token-only Supabase client (`lib/supabase.ts`).
 * - `identity`: "Confirm it's you" through the sync daemon's browser sign-in. This process holds no
 *   refresh token and `supabase.auth` throws by construction (D17), so the password and email-code
 *   paths of `createSupabaseIdentity` cannot exist here; a fresh browser sign-in can.
 * - `nativeImport`: the Tauri passkey importer (`native_vault_file_import`); the file chooser,
 *   parsing and keys stay native, the panel only ever sees counts and titles.
 * - `nativeProvider`: the macOS AutoFill provider's state; its setup lives in Settings → Vault.
 *
 * No `passkeys` door: approving a browser with a passkey needs an `aimatrx.com` page, and this
 * webview is not one — the package hides that action when the door is absent.
 */

import { createApi, type Api } from "@ai-matrx/api";
import type { IdentityAccount, VaultHostCapabilities, VaultIdentity } from "@ai-matrx/vault";
import { getAppRuntimeConfig } from "@/lib/app-config";
import { forgetToken, getSession, getToken, signIn, subscribeSession, type SessionSnapshot } from "@/lib/custodian";
import { nativeVaultFileImport } from "@/lib/native-vault-file-import";
import { subscribeNativeVaultHostEvents } from "@/lib/native-vault-auth";
import { ACTIVE_ORGANIZATION_CHANGE_EVENT, getActiveOrganizationSnapshot } from "@/lib/org/active-org";
import type { NativeVaultProviderStatus } from "@/lib/sidecar";

type NativeImportBridge = NonNullable<VaultHostCapabilities["nativeImport"]>;
type NativeProvider = NonNullable<VaultHostCapabilities["nativeProvider"]>;

let vaultApi: Api | null = null;

/** The aidream client the Vault reads and writes through (one per window). */
export function desktopVaultApi(): Api {
  vaultApi ??= createApi({
    resolveBaseUrl: (deployment) => (deployment === "aidream" ? getAppRuntimeConfig().aidreamServerUrl : undefined),
    credentials: async () => {
      const accessToken = await getToken();
      return accessToken ? { kind: "user" as const, accessToken } : null;
    },
    organizationId: () => getActiveOrganizationSnapshot().organization?.id ?? null,
    refreshCredentials: async () => {
      forgetToken();
      return (await getToken()) !== null;
    },
  });
  return vaultApi;
}

// ── Confirm it's you ─────────────────────────────────────────────────────────

interface TokenClaims {
  sub?: string;
  iat?: number;
  email?: string;
  app_metadata?: { provider?: string; providers?: string[] };
}

function claimsOf(token: string | null): TokenClaims {
  const payload = token?.split(".")[1];
  if (!payload) return {};
  try {
    return JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/"))) as TokenClaims;
  } catch {
    return {};
  }
}

/** How long a browser sign-in may take before the dialog stops waiting for it. */
const FRESH_SIGN_IN_WAIT_MS = 5 * 60_000;
const SAME_PERSON = "That signed in a different account. Use the account this vault belongs to.";

/**
 * Resolves once the daemon holds a token for `userId` issued after `issuedAfter`. The cached token
 * is dropped only when the daemon's session actually moved (`since`), so other requests in this
 * window are never starved while the person is in the browser.
 */
function waitForFreshToken(userId: string, issuedAfter: number, sinceBefore: string | null): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    let settled = false;
    const finish = (error?: Error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      clearInterval(poll);
      off();
      if (error) reject(error);
      else resolve();
    };
    const check = async (snapshot?: SessionSnapshot) => {
      const now = snapshot ?? (await getSession());
      if (settled || !now.signed_in || now.since === sinceBefore) return;
      forgetToken();
      const claims = claimsOf(await getToken());
      if (!claims.sub) return;
      if (claims.sub !== userId) finish(new Error(SAME_PERSON));
      else if ((claims.iat ?? 0) > issuedAfter) finish();
    };
    const off = subscribeSession((snapshot) => void check(snapshot));
    // A slow poll covers a re-grant the event stream never names.
    const poll = setInterval(() => void check(), 3_000);
    const timer = setTimeout(() => finish(new Error("The browser sign-in did not finish. Try again.")), FRESH_SIGN_IN_WAIT_MS);
  });
}

/** The browser sign-in, offered as the one way to confirm (the dialog labels it "Continue with Browser"). */
const BROWSER = "browser";

export const desktopVaultIdentity: VaultIdentity = {
  async account(): Promise<IdentityAccount> {
    const session = await getSession();
    if (!session.signed_in) throw new Error("You are signed out. Sign in again.");
    // `email: null` keeps the email-code offer off: this process cannot hold the session it makes.
    return { email: null, providers: [BROWSER], hasPassword: false };
  },
  async withProvider() {
    const claims = claimsOf(await getToken());
    if (!claims.sub) throw new Error("You are signed out. Sign in again.");
    const before = await getSession();
    const fresh = waitForFreshToken(claims.sub, claims.iat ?? 0, before.since);
    await signIn();
    await fresh;
  },
  withPassword() {
    return Promise.reject(new Error("Confirm in your browser instead."));
  },
  sendEmailCode() {
    return Promise.reject(new Error("Confirm in your browser instead."));
  },
  withEmailCode() {
    return Promise.reject(new Error("Confirm in your browser instead."));
  },
};

// ── Native passkey import ────────────────────────────────────────────────────

export const desktopNativeImport: NativeImportBridge = {
  request: (request) => nativeVaultFileImport(request),
  /** Fires when the signed-in account or this window's organization changes. */
  subscribe(listener) {
    let subject: string | null | undefined;
    const offAccount = subscribeNativeVaultHostEvents((envelope) => {
      const next = envelope.session?.user.id ?? null;
      if (subject !== undefined && next !== subject) listener();
      subject = next;
    });
    window.addEventListener(ACTIVE_ORGANIZATION_CHANGE_EVENT, listener);
    return () => {
      offAccount();
      window.removeEventListener(ACTIVE_ORGANIZATION_CHANGE_EVENT, listener);
    };
  },
};

// ── macOS AutoFill provider ──────────────────────────────────────────────────

/** The provider's state as the Vault shows it; `null` = this is not the desktop shell or it could not answer. */
export function nativeProviderStatusOf(status: NativeVaultProviderStatus | null): NativeProvider["status"] {
  if (!status || !status.supported || status.artifact !== "built") return "unavailable";
  if (status.os_enablement === "enabled") return "ready";
  return status.enable_action === "available" || status.settings_action === "available" ? "needs_setup" : "unavailable";
}
