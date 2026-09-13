import { beforeEach, expect, it, vi } from "vitest";
import { engine } from "./api";
const api = engine as any;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const context = (current = true) => ({ revision: 1, nextSubject: null, isCurrent: () => current });
beforeEach(() => { api.baseUrl = "http://engine-a"; api.acceptedSessionFence = { origin:"http://engine-a", generation:"g-a", credential_revision:2, subject:"actor-a" }; vi.stubGlobal("fetch", vi.fn()); });
it("retries a matching failed cleanup with a closed receipt and settled metadata", async () => { const f=vi.mocked(fetch); f.mockResolvedValueOnce(new Response("",{status:503})).mockResolvedValueOnce(json({generation:"g-p",credential_revision:0,subject:null,cleanup:{retired_generation:"g-a",status:"failed"}})).mockResolvedValueOnce(json({status:"ok",generation:"g-next",credential_revision:0})).mockResolvedValueOnce(json({generation:"g-next",credential_revision:0,subject:null,cleanup:null})); await api.clearPythonToken(context()); expect(f).toHaveBeenCalledTimes(4); expect(api.acceptedSessionFence).toBeNull(); });
it("never deletes a different actor after response loss", async () => { const f=vi.mocked(fetch); f.mockResolvedValueOnce(new Response("",{status:409})).mockResolvedValueOnce(json({generation:"g-b",credential_revision:1,subject:"actor-b",cleanup:null})); await expect(api.clearPythonToken(context())).rejects.toThrow("superseded"); expect(f).toHaveBeenCalledTimes(2); });
it("rejects stale contexts before send", async () => { await expect(api.clearPythonToken(context(false))).rejects.toThrow("no longer current"); expect(fetch).not.toHaveBeenCalled(); });
it("running cleanup polls then remains fenced", async () => { const f=vi.mocked(fetch); f.mockResolvedValueOnce(new Response("",{status:503})); for(let i=0;i<11;i++) f.mockResolvedValueOnce(json({generation:"g-p",credential_revision:0,subject:null,cleanup:{retired_generation:"g-a",status:"running"}})); await expect(api.clearPythonToken(context())).rejects.toThrow("timed out"); expect(f).toHaveBeenCalledTimes(12); });
it("lost success settles a proven empty engine without another delete", async () => { const f=vi.mocked(fetch); f.mockResolvedValueOnce(new Response("",{status:503})).mockResolvedValueOnce(json({generation:"g-new",credential_revision:0,subject:null,cleanup:null})); await api.clearPythonToken(context()); expect(f).toHaveBeenCalledTimes(2); expect(api.acceptedSessionFence).toBeNull(); });
it("allows same actor revision advance but rejects regression", async () => { const f=vi.mocked(fetch); f.mockResolvedValueOnce(new Response("",{status:409})).mockResolvedValueOnce(json({generation:"g-a",credential_revision:3,subject:"actor-a",cleanup:null})).mockResolvedValueOnce(json({status:"ok",generation:"g-new",credential_revision:0})).mockResolvedValueOnce(json({generation:"g-new",credential_revision:0,subject:null,cleanup:null})); await api.clearPythonToken(context()); api.acceptedSessionFence={origin:"http://engine-a",generation:"g-a",credential_revision:2,subject:"actor-a"}; f.mockReset().mockResolvedValueOnce(new Response("",{status:409})).mockResolvedValueOnce(json({generation:"g-a",credential_revision:1,subject:"actor-a",cleanup:null})); await expect(api.clearPythonToken(context())).rejects.toThrow("regressed"); });
it("bounds retries at three and preserves fence", async () => { const f=vi.mocked(fetch); for(let i=0;i<3;i++) f.mockResolvedValueOnce(new Response("",{status:503})).mockResolvedValueOnce(json({generation:"g-a",credential_revision:2,subject:"actor-a",cleanup:null})); await expect(api.clearPythonToken(context())).rejects.toThrow("did not finish"); expect(api.acceptedSessionFence).not.toBeNull(); });
it("changed origin sends zero delete", async () => { api.baseUrl="http://engine-b"; await expect(api.clearPythonToken(context())).rejects.toThrow("origin changed"); expect(fetch).not.toHaveBeenCalled(); });
it("treats reachable malformed metadata and a wrong nonnull alignment subject as fenced failures", async () => {
  const f = vi.mocked(fetch);
  f.mockResolvedValueOnce(json({ generation: "g", credential_revision: -1, subject: null, cleanup: null }));
  await expect(api.prepareSessionTransition(context())).resolves.toEqual({ status: "cleanup_failed" });
  f.mockReset();
  f.mockResolvedValueOnce(json({ generation: "g-b", credential_revision: 1, subject: "actor-b", cleanup: null }));
  await expect(api.prepareSessionTransition({ revision: 1, nextSubject: "actor-a", isCurrent: () => true })).resolves.toEqual({ status: "cleanup_failed" });
});
it("rejects origin-bound cloud configuration when discovery changes after metadata", async () => {
  const f = vi.mocked(fetch);
  const current = { revision: 1, nextSubject: "actor-a", isCurrent: () => true, engineOrigin: "http://engine-a", engineGeneration: "g-a", engineCredentialRevision: 0 };
  f.mockImplementationOnce(async () => { api.baseUrl = "http://engine-b"; return json({ generation: "g-a", credential_revision: 0, subject: null, cleanup: null }); });
  await expect(api.configureCloudSync("jwt", "actor-a", current)).rejects.toThrow("no longer current");
  expect(f).toHaveBeenCalledTimes(1);
});
