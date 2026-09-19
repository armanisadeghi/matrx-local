/** Synchronize the existing desktop organization choice to the local engine.
 *
 * This module holds no credential or durable revision.  The engine owns the
 * revision; this side only serializes the latest observable selection so an
 * older picker/network completion cannot overwrite a newer choice.
 */
import { getAuthedSession, subscribeSession } from "@/lib/custodian";
import {
  ACTIVE_ORGANIZATION_CHANGE_EVENT,
  getActiveOrganizationId,
} from "@/lib/org/active-org";

type Context = {
  engine_boot_id: string;
  revision: number;
  organization_id: string | null;
};

let engineUrl: string | null = null;
let generation = 0;
let chain: Promise<void> = Promise.resolve();
let listening = false;

function current(ticket: number): boolean {
  return ticket === generation;
}

async function request(
  url: string,
  token: string,
  init?: RequestInit,
): Promise<Response> {
  return fetch(`${url}/local-browser/context`, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
}

async function synchronize(ticket: number): Promise<void> {
  const url = engineUrl;
  if (!url || !current(ticket)) return;
  const session = await getAuthedSession();
  if (!session || !current(ticket) || url !== engineUrl) return;
  if (!current(ticket) || url !== engineUrl) return;
  const fresh = await getAuthedSession();
  if (
    !fresh ||
    fresh.access_token !== session.access_token ||
    fresh.user.id !== session.user.id ||
    !current(ticket) ||
    url !== engineUrl
  ) return;

  // One conflict retry is safe only after re-reading all current inputs.  A
  // later selection/auth/engine transition has already advanced generation.
  for (let attempt = 0; attempt < 2 && current(ticket) && url === engineUrl; attempt += 1) {
    const read = await request(url, fresh.access_token);
    if (!read.ok || !current(ticket) || url !== engineUrl) return;
    const context = (await read.json()) as Context;
    const latestSession = await getAuthedSession();
    const latestOrganizationId = await getActiveOrganizationId();
    if (
      !latestSession ||
      latestSession.access_token !== fresh.access_token ||
      latestSession.user.id !== fresh.user.id ||
      !current(ticket) ||
      url !== engineUrl
    ) return;
    if (context.organization_id === latestOrganizationId) return;
    const write = await request(url, latestSession.access_token, {
      method: "POST",
      body: JSON.stringify({
        engine_boot_id: context.engine_boot_id,
        expected_revision: context.revision,
        organization_id: latestOrganizationId,
      }),
    });
    if (write.ok || write.status !== 409) return;
  }
}

/** Queue a latest-only synchronization after an engine, auth, or picker event. */
export function synchronizeLocalBrowserContext(url: string | null): void {
  engineUrl = url;
  const ticket = ++generation;
  chain = chain.then(() => synchronize(ticket), () => synchronize(ticket));
}

/** Begin exactly one listener set for this renderer process. */
export function startLocalBrowserContextSynchronization(): void {
  if (listening) return;
  listening = true;
  window.addEventListener(ACTIVE_ORGANIZATION_CHANGE_EVENT, () => {
    synchronizeLocalBrowserContext(engineUrl);
  });
  subscribeSession(() => synchronizeLocalBrowserContext(engineUrl));
}

/** Test-only reset; no application state is persisted here. */
export function resetLocalBrowserContextSynchronizationForTest(): void {
  engineUrl = null;
  generation = 0;
  chain = Promise.resolve();
  listening = false;
}
