import { useEffect, useMemo } from "react";

import { useOptionalAccessHealthContext } from "@/contexts/AccessHealthContext";
import { useOptionalDownloadManager } from "@/contexts/DownloadManagerContext";
import { useOptionalPermissionsContext } from "@/contexts/PermissionsContext";
import type { PermissionKey } from "@/hooks/use-permissions";

import { registerActionNeededHandler } from "./actions";
import {
  actionNeededFromAccessResource,
  actionNeededFromLiveDownload,
} from "./adapters";
import { actionNeededStore } from "./store";

/** Adapts existing authoritative stores; it never performs its own polling. */
export function ActionNeededSources() {
  const downloads = useOptionalDownloadManager();
  const access = useOptionalAccessHealthContext();
  const permissions = useOptionalPermissionsContext();
  const accessHealth = access?.health;

  const downloadItems = useMemo(
    () =>
      (downloads?.downloads ?? [])
        .map(actionNeededFromLiveDownload)
        .filter((item) => item != null),
    [downloads?.downloads],
  );
  const accessItems = useMemo(
    () =>
      accessHealth
        ? access.degradedResources
            .map((resource) =>
              actionNeededFromAccessResource(
                resource,
                accessHealth,
                access.parentFdaProbe,
              ),
            )
            .filter((item) => item != null)
        : [],
    [accessHealth, access?.degradedResources, access?.parentFdaProbe],
  );

  useEffect(() => {
    actionNeededStore.reconcileLocal("downloads", downloadItems);
  }, [downloadItems]);
  useEffect(() => {
    actionNeededStore.reconcileLocal("access-health", accessItems);
  }, [accessItems]);

  useEffect(() => {
    const cleanups: Array<() => void> = [];
    if (permissions) {
      cleanups.push(registerActionNeededHandler("open_os_settings", async (item) => {
        if (!item.action.permission_key) return;
        const key = item.action.permission_key as PermissionKey;
        if (!permissions.permissions.has(key)) {
          console.error(`[action-needed] unknown permission key: ${key}`);
          return;
        }
        await permissions.openSettings(key);
      }));
      cleanups.push(registerActionNeededHandler("request_os_permission", async (item) => {
        if (!item.action.permission_key) return;
        const key = item.action.permission_key as PermissionKey;
        if (!permissions.permissions.has(key)) {
          console.error(`[action-needed] unknown permission key: ${key}`);
          return;
        }
        if ((await permissions.check(key)) === "granted") {
          actionNeededStore.resolve(item.fingerprint);
          return;
        }
        await permissions.request(key);
        if ((await permissions.check(key)) === "granted") {
          actionNeededStore.resolve(item.fingerprint);
        }
      }));
    }
    if (access) {
      cleanups.push(registerActionNeededHandler("recheck_access", async (item) => {
        const opts = item.action.resource_ids
          ? { resourceIds: item.action.resource_ids }
          : undefined;
        await access.actions.recheck(opts);
      }));
      cleanups.push(registerActionNeededHandler("create_access_folder", async (item) => {
        await access.actions.recheck({
          ...(item.action.resource_ids
            ? { resourceIds: item.action.resource_ids }
            : {}),
          createMissing: true,
        });
      }));
    }
    if (downloads) {
      cleanups.push(registerActionNeededHandler("open_downloads", () => downloads.openModal()));
    }
    return () => cleanups.forEach((cleanup) => cleanup());
  }, [
    access,
    downloads,
    permissions,
  ]);

  return null;
}
