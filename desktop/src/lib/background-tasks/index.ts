import { orchestrator } from "./orchestrator";
import { cloudSettingsSync, cloudHeartbeat } from "./tasks/cloud-sync";
import { hydrateSettings, syncSettings } from "./tasks/settings";
import { prefetchRemoteAccess, prefetchDevices, prefetchHardware } from "./tasks/prefetch";

orchestrator.register(cloudSettingsSync);
orchestrator.register(hydrateSettings);
orchestrator.register(syncSettings);
orchestrator.register(cloudHeartbeat);
orchestrator.register(prefetchRemoteAccess);
orchestrator.register(prefetchDevices);
orchestrator.register(prefetchHardware);

export function startBackgroundTasks(): void {
  orchestrator.start();
}

export function stopBackgroundTasks(): void {
  orchestrator.stop();
}

export { orchestrator } from "./orchestrator";
export type { BackgroundTask } from "./orchestrator";
