import { AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { dispatchActionNeeded } from "./actions";
import type { ActionNeeded } from "./types";

export function ActionNeededCard({
  item,
  compact = false,
  className,
}: {
  item: ActionNeeded;
  compact?: boolean;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex items-start gap-3 rounded-lg border border-amber-300/70 bg-amber-50/90 p-3 text-amber-950 dark:border-amber-800/60 dark:bg-amber-950/35 dark:text-amber-100",
        compact && "rounded-none border-x-0 border-t-0 px-4 py-2",
        className,
      )}
      role="status"
      data-action-needed={item.fingerprint}
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{item.title}</p>
        <p className="mt-0.5 text-xs text-amber-900/80 dark:text-amber-100/75">
          {item.message}
        </p>
      </div>
      <Button
        size="sm"
        className="h-7 shrink-0 bg-amber-600 px-2.5 text-xs text-white hover:bg-amber-700 dark:bg-amber-500 dark:text-amber-950 dark:hover:bg-amber-400"
        onClick={() => void dispatchActionNeeded(item)}
      >
        {item.action.label}
      </Button>
    </div>
  );
}
