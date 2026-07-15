/**
 * useWindowLeader — reactive view of this window's leadership status.
 *
 * The leader is the ONE full window that runs singleton services (background
 * tasks, cloud heartbeat, auto-update polling). Leadership is Rust-decided
 * and can arrive/change asynchronously (initial `get_window_role` answer, or
 * promotion via `window-leader-changed` when the previous leader closes), so
 * gate effects on this value and clean up on demotion.
 */

import { useSyncExternalStore } from "react";
import { isWindowLeader, subscribeWindowLeader } from "@/lib/window-role";

export function useWindowLeader(): boolean {
  return useSyncExternalStore(subscribeWindowLeader, isWindowLeader);
}
