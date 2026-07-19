import type { ImageGenStatus } from "@/lib/api";

/**
 * Whether the image surface needs the first-time package installer.
 *
 * An outdated or failed-to-activate managed runtime is reported as unavailable
 * by the engine, but it is still installed. Routing either state into the
 * first-time installer produces a permanent "packages are installed" success
 * panel instead of the upgrade/error experience.
 */
export function needsImageGenPackageInstall(
  status: ImageGenStatus | null,
): boolean {
  return (
    status !== null &&
    !status.available &&
    status.packages_version === null &&
    status.packages_outdated !== true
  );
}
