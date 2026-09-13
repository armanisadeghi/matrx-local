import type { BackgroundTask } from "../orchestrator";
import { engine } from "@/lib/api";
import {
  isNativeVaultHostSessionAdopted,
  nativeVaultAdoptedHostGeneration,
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
    const generation = nativeVaultAdoptedHostGeneration(session.user.id);
    if (generation === null || !isNativeVaultHostSessionAdopted(session.user.id)) return;
    if (nativeVaultAdoptedHostGeneration(session.user.id) !== generation) return;
    await engine.syncTokenToPython(
      session.access_token,
      session.user.id,
      session.refresh_token ?? undefined,
      session.expires_in ?? undefined,
    );
  },
};
