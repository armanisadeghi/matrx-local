import { cn } from "@/lib/utils";

declare const __APP_VERSION__: string;

/**
 * The application version baked into the renderer from the release authority
 * in the repository root (`pyproject.toml`). Do not read package manifests in
 * UI code; every visible version must consume this export or AppVersion.
 */
export const APP_VERSION = __APP_VERSION__;

interface AppVersionProps {
  className?: string;
  prefix?: "v" | "Version ";
}

export function AppVersion({ className, prefix = "v" }: AppVersionProps) {
  return (
    <span
      className={cn("tabular-nums", className)}
      title={`Matrx Local version ${APP_VERSION}`}
    >
      {prefix}{APP_VERSION}
    </span>
  );
}
