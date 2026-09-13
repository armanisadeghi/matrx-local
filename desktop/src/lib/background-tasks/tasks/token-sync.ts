import type { BackgroundTask } from "../orchestrator";
import { engine } from "@/lib/api";
import {
  nativeVaultEngineTransitionContext,
} from "@/lib/native-vault-auth";
import supabase from "@/lib/supabase";

export const pushTokenToPython: BackgroundTask = {
  id: "push-token-to-python",
  label: "Push auth token to Python engine",
  priority: 5,
  async fn() {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session?.access_token || !session?.user?.id) return;
    // The queue can outlive an auth event by one idle turn. The exact subject
    // and generation must still be adopted after getSession() and immediately
    // before the token crosses to the sidecar.
    const context = nativeVaultEngineTransitionContext(session.user.id);
    if (!context || !context.isCurrent()) return;
    await engine.syncTokenToPython(
      session.access_token,
      session.user.id,
      context,
      session.refresh_token ?? undefined,
      session.expires_in ?? undefined,
    );
  },
};
