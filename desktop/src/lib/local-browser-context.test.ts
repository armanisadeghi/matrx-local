import { afterEach, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  token: "token-a",
  user: "user-a",
  organization: "org-a" as string | null,
}));

vi.mock("@/lib/custodian", () => ({
  getAuthedSession: vi.fn(async () => ({ access_token: state.token, user: { id: state.user } })),
  subscribeSession: vi.fn(() => () => undefined),
}));
vi.mock("@/lib/org/active-org", () => ({
  ACTIVE_ORGANIZATION_CHANGE_EVENT: "org-change",
  getActiveOrganizationId: vi.fn(async () => state.organization),
}));

afterEach(() => {
  vi.restoreAllMocks();
  state.token = "token-a";
  state.user = "user-a";
  state.organization = "org-a";
});

it("writes the currently selected organization with engine CAS fields", async () => {
  const fetch = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ engine_boot_id: "boot", revision: 4, organization_id: null }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ engine_boot_id: "boot", revision: 5, organization_id: "org-a" }), { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  const context = await import("./local-browser-context");
  context.resetLocalBrowserContextSynchronizationForTest();
  context.synchronizeLocalBrowserContext("http://127.0.0.1:22240");
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(fetch.mock.calls[1]![1]).toMatchObject({
    method: "POST",
    body: JSON.stringify({ engine_boot_id: "boot", expected_revision: 4, organization_id: "org-a" }),
  });
});

it("never installs a selection captured before a newer transition", async () => {
  let release!: () => void;
  const delayedRead = new Promise<Response>((resolve) => { release = () => resolve(new Response(JSON.stringify({ engine_boot_id: "boot", revision: 1, organization_id: null }), { status: 200 })); });
  const fetch = vi.fn()
    .mockReturnValueOnce(delayedRead)
    .mockResolvedValueOnce(new Response(JSON.stringify({ engine_boot_id: "boot", revision: 2, organization_id: "org-b" }), { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  const context = await import("./local-browser-context");
  context.resetLocalBrowserContextSynchronizationForTest();
  context.synchronizeLocalBrowserContext("http://127.0.0.1:22240");
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  state.organization = "org-b";
  context.synchronizeLocalBrowserContext("http://127.0.0.1:22240");
  release();
  await new Promise((resolve) => setTimeout(resolve, 0));
  // The first read completed after the newer transition and therefore never
  // sent its stale POST. The second run is the current selection's GET.
  expect(fetch).toHaveBeenCalledTimes(2);
});

it("aborts a hung old read so a newer selection completes promptly", async () => {
  let oldAborted = false;
  const fetch = vi.fn()
    .mockImplementationOnce((_url: string, init: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init.signal?.addEventListener("abort", () => {
        oldAborted = true;
        reject(new DOMException("aborted", "AbortError"));
      }, { once: true });
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ engine_boot_id: "boot", revision: 3, organization_id: null }), { status: 200 }))
    .mockResolvedValueOnce(new Response("", { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  const context = await import("./local-browser-context");
  context.resetLocalBrowserContextSynchronizationForTest();
  context.synchronizeLocalBrowserContext("http://127.0.0.1:22240");
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  state.organization = "org-b";
  context.synchronizeLocalBrowserContext("http://127.0.0.1:22240");
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(oldAborted).toBe(true);
  expect(fetch).toHaveBeenCalledTimes(3);
  expect(fetch.mock.calls[2]![1]).toMatchObject({
    method: "POST",
    body: JSON.stringify({ engine_boot_id: "boot", expected_revision: 3, organization_id: "org-b" }),
  });
});

it("retries a CAS conflict only with a fresh current selection", async () => {
  const fetch = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ engine_boot_id: "boot", revision: 1, organization_id: null }), { status: 200 }))
    .mockResolvedValueOnce(new Response("", { status: 409 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ engine_boot_id: "boot", revision: 2, organization_id: null }), { status: 200 }))
    .mockResolvedValueOnce(new Response("", { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  const context = await import("./local-browser-context");
  context.resetLocalBrowserContextSynchronizationForTest();
  context.synchronizeLocalBrowserContext("http://127.0.0.1:22240");
  await new Promise((resolve) => setTimeout(resolve, 0));
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(fetch.mock.calls[3]![1]).toMatchObject({
    body: JSON.stringify({ engine_boot_id: "boot", expected_revision: 2, organization_id: "org-a" }),
  });
});
