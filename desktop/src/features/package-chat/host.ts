/**
 * Matrx Local's host for `@ai-matrx/chat` (lanes/agents-chat-proposal.md L1): the ONE token-only
 * Supabase client (`accessToken` = the custodian's token, the same function the client was built
 * with), the aidream server at the remote-config address with the window's organization, the
 * window's organization from `active-org.ts` (the only store), an in-memory chat address (the app's
 * router already owns `/chat`), and `local.cloud_chat` as the mandate behind a new chat.
 *
 * Desktop tools need no port: the package leaves every `local_*` call to this computer's engine,
 * which executes it and resumes the turn (the engine's delegation loop), and watches it settle.
 * `google_email_send` is engine-held too (the engine parks it for the person's review).
 * L2: `server.localEngine` names this engine, so a conversation bound to this computer runs on it;
 * `routing` keeps the Confidential pin per conversation on this device.
 */

import { applyOrganizationContextHeader } from "@ai-matrx/agents/matrx";
import type { ChatHost, ChatLocalEngine, ChatOrgPort, ChatOrganization, ChatRoutingPort } from "@ai-matrx/chat/host";
import { createMemoryNavigation } from "@ai-matrx/chat/host";
import { getAIDreamServerUrl } from "@/lib/app-config";
import { getToken } from "@/lib/custodian";
import { DEFAULT_CHAT_MANDATE_KEY } from "@/lib/mandates";
import {
  getActiveOrganizationSnapshot,
  requireActiveOrganizationId,
  requestOrganizationPicker,
  setActiveOrganization,
  subscribeActiveOrganization,
} from "@/lib/org/active-org";
import supabase from "@/lib/supabase";
import { engine } from "@/lib/api";

const ADDRESS_KEY = "matrx-local.package-chat.address";

/** The window's organization (active-org.ts), as the package's synchronous org port. */
const windowOrg: ChatOrgPort = {
  active(): ChatOrganization | null {
    const org = getActiveOrganizationSnapshot().organization;
    return org ? { id: org.id, name: org.name } : null;
  },
  subscribe: subscribeActiveOrganization,
  async require() {
    try {
      return await requireActiveOrganizationId();
    } catch (cause) {
      // The window's own picker is the one control; the package never picks for the person.
      requestOrganizationPicker();
      throw cause;
    }
  },
  choose(org) {
    void setActiveOrganization(org.id);
  },
};

const CONFIDENTIAL_KEY = "matrx-local.package-chat.confidential";

/** Confidential pins live on this device (where the text lives); the package's resolver enforces them. */
function createDeviceRouting(): ChatRoutingPort {
  const read = (): Set<string> => {
    try {
      const raw = window.localStorage.getItem(CONFIDENTIAL_KEY);
      return new Set(raw ? (JSON.parse(raw) as string[]) : []);
    } catch {
      return new Set();
    }
  };
  let pinned = read();
  const listeners = new Set<() => void>();
  return {
    policy: (conversationId) => (pinned.has(conversationId) ? "local-only" : "any"),
    set(conversationId, policy) {
      pinned = new Set(pinned);
      if (policy === "local-only") pinned.add(conversationId);
      else pinned.delete(conversationId);
      try {
        window.localStorage.setItem(CONFIDENTIAL_KEY, JSON.stringify([...pinned]));
      } catch {
        /* no local storage — the pin holds for this window only */
      }
      listeners.forEach((listener) => listener());
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

/** This engine as the package sees it: its address and what `/health` says it can run. */
function createLocalEngine(): () => ChatLocalEngine | null {
  let known: ChatLocalEngine | null = null;
  let checkedAt = 0;
  const refresh = async (url: string) => {
    checkedAt = Date.now();
    try {
      const body = (await fetch(`${url}/health`).then((r) => r.json())) as { capabilities?: unknown };
      const capabilities = Array.isArray(body.capabilities)
        ? body.capabilities.filter((c): c is string => typeof c === "string")
        : [];
      known = { baseUrl: url, capabilities };
    } catch {
      known = null;
    }
  };
  const current = () => {
    const url = engine.engineUrl?.replace(/\/+$/, "") ?? null;
    if (!url) return null;
    if (known?.baseUrl !== url || Date.now() - checkedAt > 30_000) void refresh(url);
    return known?.baseUrl === url ? known : null;
  };
  current();
  return current;
}

function readAddress(): string | null {
  try {
    return window.sessionStorage.getItem(ADDRESS_KEY);
  } catch {
    return null;
  }
}

/** Build the host once the server address is known. */
export async function createDesktopChatHost(options?: {
  /** Open this conversation first (`/cloud-chat?chat=package&conversation=<id>`). */
  conversationId?: string | null | undefined;
}): Promise<ChatHost> {
  const baseUrl = await getAIDreamServerUrl();
  const initial = options?.conversationId ? `/chat/${options.conversationId}` : readAddress();
  return {
    db: supabase,
    accessToken: getToken,
    sourceApp: "matrx-local",
    app: {
      sourceApp: "matrx-desktop",
      sourceFeature: "chat-route",
      defaultChatMandateKey: DEFAULT_CHAT_MANDATE_KEY,
    },
    org: windowOrg,
    engineHeldTools: ["google_email_send"],
    routing: createDeviceRouting(),
    server: {
      baseUrl: () => baseUrl,
      localEngine: createLocalEngine(),
      async headers() {
        let headers: Record<string, string> = {};
        const token = await getToken();
        if (token) headers.Authorization = `Bearer ${token}`;
        const org = windowOrg.active();
        if (org) headers = applyOrganizationContextHeader(headers, org.id);
        return headers;
      },
    },
    navigation: createMemoryNavigation({
      initial,
      persist: (address) => {
        try {
          window.sessionStorage.setItem(ADDRESS_KEY, address);
        } catch {
          /* no session storage — the address is kept in memory only */
        }
      },
    }),
  };
}
