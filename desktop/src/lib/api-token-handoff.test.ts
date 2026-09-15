import { beforeEach, expect, it, vi } from "vitest";
import { engine } from "./api";

const api = engine as any;
const context = {
  revision: 1, nextSubject: "actor-a", isCurrent: () => true,
  engineOrigin: "http://engine.test", engineGeneration: "1", engineCredentialRevision: 1,
};
beforeEach(() => {
  api.baseUrl = "http://engine.test";
  engine.setTokenProvider(async () => "current-access");
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ status: "ok" }))));
});
for (const method of ["configureCloudSync", "reconfigureCloudSync"] as const) {
  it(`${method} refuses another account without network I/O`, async () => {
    await expect(engine[method]("current-access", "actor-b", context)).rejects.toThrow("no longer current");
    expect(fetch).not.toHaveBeenCalled();
  });
  it(`${method} rechecks authority after resolving the daemon token`, async () => {
    let current = true;
    engine.setTokenProvider(async () => { current = false; return "current-access"; });
    await expect(engine[method]("current-access", "actor-a", { ...context, isCurrent: () => current })).rejects.toThrow("no longer current");
    expect(fetch).not.toHaveBeenCalled();
  });
  it(`${method} sends the current access token without installing a session`, async () => {
    await engine[method]("current-access", "actor-a", context);
    expect(fetch).toHaveBeenCalledOnce();
    const [url, init] = vi.mocked(fetch).mock.calls[0]!;
    expect(String(url)).toMatch(/\/cloud\/(re)?configure$/);
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer current-access");
  });
}

it("keeps engine generation across token rotation and changes it on a same-port restart", async () => {
  const { engine } = await import("./api");
  await engine.discover("http://engine.test");
  let instance = "process-a";
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ boot_id: instance })));
  const first = await engine.prepareSessionTransition({ revision: 1, nextSubject: "user-a", isCurrent: () => true });
  const refresh = await engine.prepareSessionTransition({ revision: 2, nextSubject: "user-a", isCurrent: () => true });
  expect(first).toMatchObject({ generation: "process-a", credentialRevision: 1 });
  expect(refresh).toMatchObject({ generation: "process-a", credentialRevision: 2 });
  instance = "process-b";
  expect(await engine.prepareSessionTransition({ revision: 3, nextSubject: "user-a", isCurrent: () => true })).toMatchObject({ generation: "process-b" });
});
