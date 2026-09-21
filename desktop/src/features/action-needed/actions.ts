import { isFullWindow } from "@/lib/window-role";
import { invokeTauri, isTauri } from "@/lib/sidecar";

import type { ActionNeeded, ActionNeededAction, ActionNeededChoice } from "./types";

type ActionHandler = (item: ActionNeeded) => void | Promise<void>;
const handlers = new Map<string, ActionHandler>();

export function registerActionNeededHandler(
  kind: string,
  handler: ActionHandler,
): () => void {
  handlers.set(kind, handler);
  return () => {
    if (handlers.get(kind) === handler) handlers.delete(kind);
  };
}

export interface NavigationRuntime {
  fullWindow: boolean;
  tauri: boolean;
  setHash: (hash: string) => void;
  focus: (label: string) => Promise<void>;
  openPeer: () => Promise<string>;
  emitTo: (label: string, event: string, payload: string) => Promise<void>;
  persistPendingRoute: (route: string) => void;
  persistPendingAction: (item: ActionNeeded, handoffId: string) => void;
}

export const ACTION_NEEDED_PENDING_ROUTE_KEY = "matrx-action-needed-pending-route";
export const ACTION_NEEDED_PENDING_ACTION_KEY = "matrx-action-needed-pending-action";

function defaultNavigationRuntime(): NavigationRuntime {
  return {
    fullWindow: isFullWindow,
    tauri: isTauri(),
    setHash: (hash) => {
      window.location.hash = hash;
    },
    focus: (label) => invokeTauri<void>("focus_app_window", { label }),
    openPeer: () => invokeTauri<string>("open_peer_window"),
    emitTo: async (label, event, payload) => {
      const { emitTo } = await import("@tauri-apps/api/event");
      await emitTo(label, event, payload);
    },
    persistPendingRoute: (route) => {
      localStorage.setItem(
        ACTION_NEEDED_PENDING_ROUTE_KEY,
        JSON.stringify({ route, requestedAt: Date.now() }),
      );
    },
    persistPendingAction: (item, handoffId) => {
      localStorage.setItem(
        ACTION_NEEDED_PENDING_ACTION_KEY,
        JSON.stringify({ item, handoffId, requestedAt: Date.now() }),
      );
    },
  };
}

/** Navigate locally in full windows; panels hand the exact route to a full window. */
export async function navigateForActionNeeded(
  route: string,
  runtime: NavigationRuntime = defaultNavigationRuntime(),
): Promise<void> {
  const normalized = route.startsWith("/") ? route : `/${route}`;
  if (runtime.fullWindow || !runtime.tauri) {
    runtime.setHash(`#${normalized}`);
    return;
  }
  // The event handles an already-running full window. Persistence closes the
  // race where a newly-created peer has not mounted its listener yet.
  runtime.persistPendingRoute(normalized);
  let target = "main";
  try {
    await runtime.focus(target);
  } catch {
    target = await runtime.openPeer();
  }
  await runtime.emitTo(target, "action-needed://navigate", normalized);
}

async function focusOrOpenPeer(runtime: NavigationRuntime): Promise<string> {
  let target = "main";
  try {
    await runtime.focus(target);
  } catch {
    target = await runtime.openPeer();
  }
  return target;
}

async function dispatchBuiltIn(
  action: ActionNeededAction,
  runtime: NavigationRuntime,
): Promise<boolean> {
  if (action.kind === "open_url" && action.url) {
    if (isTauri()) {
      const { open } = await import("@tauri-apps/plugin-shell");
      await open(action.url);
    } else {
      window.open(action.url, "_blank", "noopener,noreferrer");
    }
    return true;
  }
  if (action.route) {
    await navigateForActionNeeded(action.route, runtime);
    return true;
  }
  return false;
}

/** How a chosen option reaches the engine. Injectable for tests. */
export interface ChoiceTransport {
  put: (route: string, body: unknown) => Promise<unknown>;
}

async function defaultChoiceTransport(): Promise<ChoiceTransport> {
  // Lazy: `@/lib/api` is the app's heaviest module.
  const { engine } = await import("@/lib/api");
  return { put: (route, body) => engine.put(route, body) };
}

/**
 * Submit the person's pick for an item that carries `action.choices`.
 *
 * ONE transport for every choice-shaped item: PUT `{ choice: id }` to the
 * item's `choice_route` on the engine. The engine route is owned by the
 * source that raised the item (it stores the answer wherever it belongs and
 * withdraws the card on the next snapshot), so nothing here knows what the
 * choice means. A refusal is thrown with the engine's own sentence so the
 * card can show it verbatim.
 */
export async function submitActionNeededChoice(
  item: ActionNeeded,
  choice: ActionNeededChoice,
  transport?: ChoiceTransport,
): Promise<void> {
  const route = item.action.choice_route;
  if (!route) {
    throw new Error(
      `[action-needed] ${item.fingerprint} offers choices but names no choice_route`,
    );
  }
  const engine = transport ?? (await defaultChoiceTransport());
  await engine.put(route, { choice: choice.id });
}

export async function dispatchActionNeeded(
  item: ActionNeeded,
  runtime: NavigationRuntime = defaultNavigationRuntime(),
): Promise<void> {
  const handler = handlers.get(item.action.kind);
  if (handler) {
    await handler(item);
    return;
  }
  if (await dispatchBuiltIn(item.action, runtime)) return;
  if (!runtime.fullWindow && runtime.tauri) {
    // Panels intentionally do not mount every heavyweight feature provider.
    // Hand the complete item to a full window so its canonical handler can run.
    const handoffId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    runtime.persistPendingAction(item, handoffId);
    const target = await focusOrOpenPeer(runtime);
    await runtime.emitTo(
      target,
      "action-needed://dispatch",
      JSON.stringify({ item, handoffId }),
    );
    return;
  }
  console.error(
    `[action-needed] no dispatcher for action kind ${item.action.kind}`,
    item,
  );
}
