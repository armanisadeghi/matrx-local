import type { ImageGenStatus } from "@/lib/api";

/**
 * Whether the image surface needs the first-time package installer.
 *
 * An outdated managed runtime is intentionally reported as unavailable by the
 * engine while its mandatory update is pending. It is still installed, so
 * routing that state into the first-time installer produces a permanent
 * "packages are installed" success panel instead of the upgrade experience.
 */
export function needsImageGenPackageInstall(
  status: ImageGenStatus | null,
): boolean {
  return (
    status !== null && !status.available && status.packages_outdated !== true
  );
}
