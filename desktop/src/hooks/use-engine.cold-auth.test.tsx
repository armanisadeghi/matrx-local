/** @vitest-environment jsdom */
import { act } from "react"; import { createRoot } from "react-dom/client"; import { expect,it,vi } from "vitest";
const mocks=vi.hoisted(()=>{const order:string[]=[]; const session={user:{id:"actor-a"},access_token:"daemon-token"};return {order,session,listener:undefined as any};});
vi.mock("@/lib/api",()=>({engine:{engineUrl:"http://engine.test",setTokenProvider:vi.fn(),isHealthy:async()=>true,discover:async()=>"http://engine.test",getPlatformContext:async()=>({}),listTools:async()=>[],getVersion:async()=>"x",getSystemInfo:async()=>({}),configureCloudSync:async()=>mocks.order.push("configure"),reconfigureCloudSync:async()=>mocks.order.push("reconfigure"),connectWebSocket:async()=>mocks.order.push("socket"),disconnect:vi.fn(),on:()=>()=>undefined,prepareSessionTransition:async()=>({status:"aligned",origin:"http://engine.test",generation:"g",credentialRevision:0,subject:"actor-a"}),cloudHeartbeat:async()=>undefined}}));
vi.mock("@/lib/local-browser-context",()=>({startLocalBrowserContextSynchronization:vi.fn(),synchronizeLocalBrowserContext:vi.fn()}));
vi.mock("@/lib/custodian",()=>({
  getAuthedSession:async()=>mocks.session,
  getToken:async()=>mocks.session.access_token,
  subscribeSession:()=>()=>undefined,
}));
vi.mock("@/lib/native-vault-auth",()=>({resolveNativeVaultEngineAccessToken:async()=>"daemon-token",isNativeVaultHostRevisionCurrent:()=>true,nativeVaultEngineTransitionContext:()=>({revision:1,isCurrent:()=>true,engineOrigin:"http://engine.test",engineGeneration:"g"}),subscribeNativeVaultHostEvents:(cb:any)=>{mocks.listener=cb;queueMicrotask(()=>cb({session:mocks.session,revision:1,completion:Promise.resolve({accepted:true})}));return()=>undefined;}}));
vi.mock("@/lib/sidecar",()=>({ENGINE_STARTUP_TIMEOUT_SECONDS:1,isTauri:()=>false,startSidecar:vi.fn(),stopSidecar:vi.fn(),waitForOwnedEngine:vi.fn()}));vi.mock("@/lib/platformCtx",()=>({initPlatformCtx:vi.fn()}));vi.mock("@/lib/background-tasks",()=>({startBackgroundTasks:()=>mocks.order.push("background"),stopBackgroundTasks:vi.fn()}));vi.mock("@/hooks/use-window-leader",()=>({useWindowLeader:()=>true}));vi.mock("@/hooks/use-client-log",()=>({emitClientLog:vi.fn()}));
import {useEngine} from "./use-engine";
it("cold configures then opens socket and starts background work",async()=>{(globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;const root=createRoot(document.createElement("div"));function S(){useEngine();return null;}await act(async()=>{root.render(<S/>);await new Promise(r=>setTimeout(r,0));});await vi.waitFor(()=>expect(mocks.order).toContain("background"));expect(mocks.order.slice(0,3)).toEqual(["configure","socket","background"]);expect(mocks.order).not.toContain("reconfigure");await act(async()=>root.unmount());});
