import type {
  AccessHealth,
  AccessResourceHealth,
} from "@/lib/api";
import type { DownloadEntry, DownloadResolution } from "@/lib/downloads/types";
import { deriveAccessPresentation } from "@/hooks/use-access-health";

import type { ActionNeeded, ActionNeededAction } from "./types";

function epoch(value: string | number | null | undefined): number {
  if (typeof value === "number") return value;
  if (!value) return Date.now();
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : Date.now();
}

function downloadAction(resolution: DownloadResolution): ActionNeededAction {
  switch (resolution.action_kind) {
    case "settings_api_keys":
      return {
        kind: "navigate",
        label: resolution.action_label,
        provider: resolution.provider,
        route: `/settings?tab=api-keys${
          resolution.provider ? `&provider=${encodeURIComponent(resolution.provider)}` : ""
        }`,
      };
    case "open_url":
      return {
        kind: "open_url",
        label: resolution.action_label,
        url: resolution.action_url,
      };
    case "install_ai_packages":
      return {
        kind: "navigate",
        label: resolution.action_label,
        route: "/media-generation",
      };
  }
}

export function actionNeededFromDownload(
  download: DownloadEntry,
): ActionNeeded | null {
  if (download.status !== "failed" || !download.resolution) return null;
  const resolution = download.resolution;
  const kind =
    resolution.action_kind === "settings_api_keys"
      ? "api_key"
      : resolution.action_kind === "open_url"
        ? "external_approval"
        : "capability_install";
  return {
    fingerprint: `download:${download.id}:${resolution.code}`,
    code: resolution.code,
    kind,
    feature: download.category,
    title: resolution.title,
    message: resolution.message,
    action: downloadAction(resolution),
    source: "downloads",
    status: "active",
    observed_at: epoch(download.updated_at),
    details: { download_id: download.id },
  };
}

export function actionNeededFromAccessResource(
  resource: AccessResourceHealth,
  health: AccessHealth,
  parentFdaProbe: boolean | null,
): ActionNeeded | null {
  if (resource.status !== "degraded") return null;
  const presentation = deriveAccessPresentation(resource, health, parentFdaProbe);
  const action: ActionNeededAction =
    presentation.primaryAction === "create_folder"
      ? {
          kind: "create_access_folder",
          label: "Create folder",
          resource_ids: [resource.resource_id],
        }
      : presentation.primaryAction === "open_settings"
        ? {
            kind: "open_os_settings",
            label: "Open System Settings",
            permission_key: "full_disk_access",
            resource_ids: [resource.resource_id],
          }
        : {
            kind: "recheck_access",
            label: "Check again",
            resource_ids: [resource.resource_id],
          };
  return {
    fingerprint: `access:${resource.resource_id}:${resource.kind ?? "unknown"}`,
    code: resource.kind ?? "filesystem_access_degraded",
    kind: "filesystem_access",
    feature: resource.resource_id,
    title: presentation.title,
    message: presentation.body,
    action,
    source: "access-health",
    status: "active",
    observed_at: resource.last_failure?.at ?? resource.generation,
    details: { resource_id: resource.resource_id },
  };
}
