/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({ subscriber: undefined as any, getSession: vi.fn(), retry: vi.fn(), current: vi.fn(() => true) }));
const signedOut = { signed_in: false, user_id: null, email: null, state: "signed_out", state_reason: null, since: null, next_attempt_at: null, cloud_state_write_pending: false };
vi.mock("@/lib/custodian", () => ({ currentSession: () => signedOut, getSession: mocks.getSession, sessionToMatrx: (s:any) => s.signed_in ? { user: { id:s.user_id, email:s.email } } : null, signIn: vi.fn(), signOut: vi.fn() }));
vi.mock("@/lib/native-vault-auth", () => ({ invalidateNativeVaultBeforeHostMutation: vi.fn(), nativeVaultAdoptedHostGeneration: vi.fn(), retryNativeVaultAccountCleanup:mocks.retry, isNativeVaultHostRevisionCurrent:mocks.current, subscribeNativeVaultHostEvents:vi.fn((cb)=>{mocks.subscriber=cb;return()=>undefined;}) }));
vi.mock("@/features/content-ir/runtime/registry",()=>({resetContentIr:vi.fn(),warmContentIr:vi.fn()})); vi.mock("@/hooks/use-client-log",()=>({emitClientLog:vi.fn()}));
import { useAuth } from "./use-auth";
let root:Root, container:HTMLDivElement, auth:ReturnType<typeof useAuth>;
function Subject(){auth=useAuth();return null;}
beforeEach(async()=>{ (globalThis as any).IS_REACT_ACT_ENVIRONMENT=true; mocks.current.mockReturnValue(true); mocks.getSession.mockResolvedValue(signedOut); container=document.createElement("div");document.body.append(container);root=createRoot(container);await act(async()=>root.render(<Subject/>)); });
afterEach(async()=>{await act(async()=>root.unmount());container.remove();mocks.getSession.mockReset();mocks.retry.mockReset();mocks.current.mockReset();});
it("preserves verified daemon identity behind a blocked recovery gate",async()=>{const snap={...signedOut,signed_in:true,user_id:"actor-a",email:"a@test"};mocks.getSession.mockResolvedValue(snap);act(()=>mocks.subscriber({event:"SIGNED_IN",session:{user:{id:"actor-a",email:"a@test"}},revision:1,snapshot:snap,completion:Promise.reject(new Error("native"))}));await act(async()=>{await new Promise((resolve)=>setTimeout(resolve,0));});expect(auth.loading).toBe(false);expect(auth.user?.id).toBe("actor-a");expect(auth.isAuthenticated).toBe(false);expect(auth.accountConnectionUnavailable).toBe(true);});
it("settles anonymous cleanup failure with retry",async()=>{act(()=>mocks.subscriber({event:"SIGNED_OUT",session:null,revision:1,snapshot:signedOut,completion:Promise.reject(new Error("native"))}));await act(async()=>{await new Promise((resolve)=>setTimeout(resolve,0));});expect(auth.loading).toBe(false);expect(auth.accountConnectionUnavailable).toBe(true);expect(auth.error).toContain("finish account cleanup");});
