import { ActionNeededCard, useActionNeeded } from "@/features/action-needed";
import type { EngineStatus } from "@/hooks/use-engine";

interface AppActionBannerProps {
  engineStatus: EngineStatus;
}

/** Persistent global renderer for the canonical, keyed remediation store. */
export function AppActionBanner(_props: AppActionBannerProps) {
  const items = useActionNeeded();
  const first = items[0];
  if (!first) return null;

  return <ActionNeededCard item={first} compact />;
}
