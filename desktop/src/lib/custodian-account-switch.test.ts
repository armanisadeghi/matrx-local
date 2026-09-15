// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from "vitest";
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(async () => ({ base_url: "http://daemon.test", read_token: "read-test", world: "dev" })) }));
const snapshot = (id: string) => ({ signed_in: true, user_id: id, email: `${id}@test.invalid`, state: "signed_in", state_reason: null, since: null, next_attempt_at: null, cloud_state_write_pending: false });
const jwt = (id: string) => `e30.${btoa(JSON.stringify({ sub: id }))}.signature`;
const grant = (id: string) => ({ access_token: jwt(id), user_id: id, expires_at: new Date(Date.now() + 3600000).toISOString() });
beforeEach(() => { vi.resetModules(); vi.unstubAllGlobals(); });
it("drops account A's cached token on a non-rotation account B event", async () => {
  let current = "a";
  let stream!: ReadableStreamDefaultController<Uint8Array>;
  const fetchMock = vi.fn(async (url: string) => {
    if (url.endsWith("/v1/events")) return new Response(new ReadableStream({ start(controller) { stream = controller; } }));
    return Response.json(url.endsWith("/v1/token") ? grant(current) : snapshot(current));
  });
  vi.stubGlobal("fetch", fetchMock);
  const c = await import("./custodian");
  expect((await c.getAuthedSession())?.access_token).toBe(jwt("a"));
  c.subscribeSession(() => undefined);
  await vi.waitFor(() => expect(stream).toBeDefined());
  current = "b";
  stream.enqueue(new TextEncoder().encode(`event: session.changed\ndata: ${JSON.stringify({ session: snapshot("b"), rotated: false })}\n\n`));
  await vi.waitFor(() => expect(c.currentSession().user_id).toBe("b"));
  expect(await c.getAuthedSession()).toMatchObject({ access_token: jwt("b"), user: { id: "b" } });
});
it("discards an old in-flight grant after the account changes", async () => {
  let current = "a";
  let resolveOld!: (r: Response) => void;
  let hold = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/v1/token") && hold) return new Promise<Response>((r) => { resolveOld = r; });
    return Response.json(url.endsWith("/v1/token") ? grant(current) : snapshot(current));
  }));
  const c = await import("./custodian");
  await c.getSession();
  const old = c.getToken();
  await vi.waitFor(() => expect(resolveOld).toBeDefined());
  current = "b";
  await c.getSession();
  hold = false;
  expect((await c.getAuthedSession())?.user.id).toBe("b");
  resolveOld(Response.json(grant("a")));
  expect(await old).toBeNull();
  expect((await c.getAuthedSession())?.access_token).toBe(jwt("b"));
});
it("refuses a grant whose bearer subject disagrees with its user", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ ...grant("a"), user_id: "b" })));
  const c = await import("./custodian");
  expect(await c.getToken()).toBeNull();
});
