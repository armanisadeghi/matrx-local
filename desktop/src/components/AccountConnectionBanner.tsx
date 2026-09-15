import { useState } from "react";
import { Button } from "@ai-matrx/design-system";

/** Authentication remains valid while a local connection is being repaired. */
export function AccountConnectionBanner({ error, onRetry }: {
  error: string | null;
  onRetry: () => Promise<void>;
}) {
  const [retrying, setRetrying] = useState(false);
  if (!error) return null;
  return (
    <div role="alert" className="flex shrink-0 items-center gap-3 border-b border-destructive/30 bg-destructive/10 px-4 py-2 text-sm">
      <div className="min-w-0 flex-1">
        <p className="font-medium">Your account is signed in. Its connection needs attention.</p>
        <p className="text-muted-foreground">{error}</p>
      </div>
      <Button size="sm" variant="outline" disabled={retrying} onClick={() => {
        setRetrying(true);
        void onRetry().finally(() => setRetrying(false));
      }}>
        {retrying ? "Reconnecting…" : "Retry connection"}
      </Button>
    </div>
  );
}
