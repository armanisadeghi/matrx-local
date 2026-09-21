import { AlertTriangle, Loader2, X } from "lucide-react";
import { useState } from "react";

import { Button } from "@ai-matrx/design-system";
import { cn } from "@/lib/utils";

import { dispatchActionNeeded, submitActionNeededChoice } from "./actions";
import { dismissActionNeeded } from "./store";
import type { ActionNeeded, ActionNeededChoice } from "./types";

export function ActionNeededCard({
  item,
  compact = false,
  className,
}: {
  item: ActionNeeded;
  compact?: boolean;
  className?: string;
}) {
  const choices = item.action.choices ?? [];
  const [choosingId, setChoosingId] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);

  const choose = async (choice: ActionNeededChoice) => {
    setChoosingId(choice.id);
    setRefusal(null);
    try {
      await submitActionNeededChoice(item, choice);
      // The source withdraws the card on its next snapshot; nothing to hide here.
    } catch (err) {
      // The engine's own sentence, verbatim — a refused choice is a fact the
      // person can act on (law 4), never a swallowed click.
      setRefusal(err instanceof Error ? err.message : "That choice was not accepted.");
    } finally {
      setChoosingId(null);
    }
  };

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
        {choices.length > 0 && (
          <div
            className="mt-2 flex flex-wrap items-center gap-1.5"
            role="group"
            aria-label={item.action.label}
          >
            {choices.map((choice) => (
              <Button
                key={choice.id}
                size="sm"
                variant="outline"
                className="h-7 border-amber-400/70 bg-white/70 px-2.5 text-xs text-amber-950 hover:bg-amber-100 dark:border-amber-700/70 dark:bg-amber-950/40 dark:text-amber-100 dark:hover:bg-amber-900/60"
                disabled={choosingId !== null}
                title={choice.description ?? undefined}
                onClick={() => void choose(choice)}
                data-action-needed-choice={choice.id}
              >
                {choosingId === choice.id ? (
                  <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
                ) : null}
                {choice.label}
              </Button>
            ))}
          </div>
        )}
        {refusal && (
          <p className="mt-1.5 text-xs text-red-700 dark:text-red-300" role="alert">
            {refusal}
          </p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-1">
        {choices.length === 0 && (
          <Button
            size="sm"
            className="h-7 bg-amber-600 px-2.5 text-xs text-white hover:bg-amber-700 dark:bg-amber-500 dark:text-amber-950 dark:hover:bg-amber-400"
            onClick={() => void dispatchActionNeeded(item)}
          >
            {item.action.label}
          </Button>
        )}
        <button
          type="button"
          onClick={() => dismissActionNeeded(item)}
          className="rounded-sm p-1 text-amber-800/70 transition-colors hover:text-amber-950 dark:text-amber-100/70 dark:hover:text-amber-50"
          aria-label={`Dismiss ${item.title}`}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}
