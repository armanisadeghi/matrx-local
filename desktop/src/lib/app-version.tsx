import { cn } from "@/lib/utils";
import { APP_VERSION } from "@/lib/app-version-constant";
import { useVersionStateOrNull } from "@/contexts/VersionStateContext";

/**
 * The application version baked into the renderer from the release authority
 * in the repository root (`pyproject.toml`). Do not read package manifests in
 * UI code; every visible version must consume this export or AppVersion.
 */
export { APP_VERSION };

interface AppVersionProps {
  className?: string;
  prefix?: "v" | "Version ";
}

/**
 * The ONE ambient version chip — status bar, startup screen, first-run screen,
 * login footer.
 *
 * It shows the build that is RUNNING, and when a newer build is already sitting
 * on disk waiting for a restart it says so inline. A bare "v1.4.145" next to a
 * disk carrying 1.4.147 is the lie this component exists to prevent, and every
 * ambient chip inherits the fix from here rather than restating it.
 *
 * Outside `VersionStateProvider` (panel windows, error boundaries) there is no
 * disk fact to compare against, so the chip shows the running build and claims
 * nothing more.
 */
export function AppVersion({ className, prefix = "v" }: AppVersionProps) {
  const versions = useVersionStateOrNull();
  const pending = versions?.restartRequired ? versions.pendingVersion : null;

  const title = pending
    ? `Running Matrx Local ${APP_VERSION}. Version ${pending.replace(/^v/i, "")} is installed and starts when you restart AI Matrx.`
    : `Matrx Local version ${APP_VERSION}`;

  return (
    <span className={cn("tabular-nums", className)} title={title}>
      {prefix}
      {APP_VERSION}
      {versions?.restartRequired && (
        <span className="ml-1 text-amber-600 dark:text-amber-400">
          · restart to finish update
        </span>
      )}
    </span>
  );
}
