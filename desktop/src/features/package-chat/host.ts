/**
 * Matrx Local's host for `@ai-matrx/chat` (lanes/agents-chat-proposal.md L1): the ONE token-only
 * Supabase client (`accessToken` = the custodian's token, the same function the client was built
 * with), the aidream server at the remote-config address with the window's organization, the
 * window's organization from `active-org.ts` (the only store), an in-memory chat address (the app's
 * router already owns `/chat`), and `local.cloud_chat` as the mandate behind a new chat.
 *
 * Desktop tools need no port: the package leaves every `local_*` call to this computer's engine,
 * which executes it and resumes the turn (the engine's delegation loop), and watches it settle.
 */

import { applyOrganizationContextHeader } from "@ai-matrx/agents/matrx";
import type { ChatHost, ChatOrgPort, ChatOrganization } from "@ai-matrx/chat/host";
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
    server: {
      baseUrl: () => baseUrl,
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
