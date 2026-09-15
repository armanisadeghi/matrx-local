import type { BackgroundTask } from "../orchestrator";
import { engine } from "@/lib/api";
import { nativeVaultEngineTransitionContext } from "@/lib/native-vault-auth";
import { getAuthedSession } from "@/lib/custodian";

async function adoptedSession() {
  const session = await getAuthedSession();
  if (!session?.access_token || !session.user?.id) return null;
  const context = nativeVaultEngineTransitionContext(session.user.id);
  return context === null ? null : { session, context };
}

function remainsAdopted(adopted: { context: { isCurrent(): boolean } }): boolean {
  return adopted.context.isCurrent();
}

export const cloudSettingsSync: BackgroundTask = {
  id: "cloud-settings-sync",
  label: "Configure cloud sync",
  priority: 10,
  async fn() {
    const adopted = await adoptedSession();
    if (!adopted || !remainsAdopted(adopted)) return;
    await engine.configureCloudSync(adopted.session.user.id, adopted.context);
  },
};

export const cloudHeartbeat: BackgroundTask = {
  id: "cloud-heartbeat",
  label: "Send cloud heartbeat",
  priority: 20,
  async fn() {
    // Heartbeats are authority-bearing cloud activity too; do not let a
    // queued idle callback survive an account fence.
    const adopted = await adoptedSession();
    if (!adopted || !remainsAdopted(adopted)) return;
    await engine.cloudHeartbeat(adopted.context);
  },
};
