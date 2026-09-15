import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { engine } from "./api";

const api = engine as any;
const context = (current = true) => ({ revision: 1, nextSubject: null, isCurrent: () => current });
beforeEach(() => {
  api.baseUrl = "http://engine-a";
  vi.stubGlobal("fetch", vi.fn());
});
afterEach(() => vi.unstubAllGlobals());

it("does not probe an obsolete transition", async () => {
  expect(await engine.prepareSessionTransition(context(false))).toEqual({ status: "superseded" });
  expect(fetch).not.toHaveBeenCalled();
});

it("refuses missing process identity instead of accepting a durable device ID", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(Response.json({ instance_id: "persistent-device" }));
  await expect(engine.prepareSessionTransition(context())).rejects.toThrow("running instance");
});

it("rejects an engine switch while its health request is pending", async () => {
  vi.mocked(fetch).mockImplementationOnce(async () => {
    api.baseUrl = "http://engine-b";
    return Response.json({ boot_id: "process-a" });
  });
  expect(await engine.prepareSessionTransition(context())).toEqual({ status: "superseded" });
});

it("rejects origin-bound cloud configuration when discovery changes during token resolution", async () => {
  const current = { revision: 1, nextSubject: "actor-a", isCurrent: () => true, engineOrigin: "http://engine-a", engineGeneration: "g-a", engineCredentialRevision: 0 };
  engine.setTokenProvider(async () => {
    api.baseUrl = "http://engine-b";
    return "current-token";
  });
  await expect(engine.configureCloudSync("actor-a", current)).rejects.toThrow("no longer current");
  expect(fetch).not.toHaveBeenCalled();
});
