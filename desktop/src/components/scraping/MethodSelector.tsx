/**
 * MethodSelector — pill-style selector for scrape method with tooltip
 * explaining what each option does and what the selector controls.
 *
 * The "Browser" method needs a Chromium build that is downloaded, not bundled.
 * When it is missing the pill is disabled and says so in plain language rather
 * than letting the user pick a method that can only fail
 * (BrowserRuntimeContext owns that state and the install that fixes it).
 */

import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { ScrapeMethod } from "@/hooks/use-scrape";
import { useOptionalBrowserRuntimeContext } from "@/contexts/BrowserRuntimeContext";

interface MethodSelectorProps {
  value: ScrapeMethod;
  onChange: (method: ScrapeMethod) => void;
  className?: string;
}

const METHODS: { id: ScrapeMethod; label: string; description: string }[] = [
  {
    id: "engine",
    label: "Engine",
    description:
      "Local Python engine using your residential IP. Best for most sites. Supports Playwright fallback for JS-heavy pages.",
  },
  {
    id: "local-browser",
    label: "Browser",
    description:
      "Playwright headless browser. Slower but handles JavaScript-rendered pages and aggressive anti-bot measures.",
  },
  {
    id: "remote",
    label: "Remote",
    description:
      "Cloud scraper server (scraper.app.matrxserver.com). Uses server-side proxy pool. Results are cached server-side for all your devices.",
  },
];

export function MethodSelector({ value, onChange, className }: MethodSelectorProps) {
  const browserRuntime = useOptionalBrowserRuntimeContext();
  // Optimistic until the probe answers — never grey out a working control
  // because a status call is still in flight.
  const browserAvailable = browserRuntime ? browserRuntime.available : true;
  const browserInstalling = browserRuntime?.installing ?? false;
  const unavailableNote = browserInstalling
    ? "Downloading the built-in browser — available when it finishes."
    : "The built-in browser isn't installed yet. Install it on this page to use this method.";

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div
          className={cn(
            "flex items-center gap-0.5 rounded-md border bg-muted/40 p-0.5",
            className,
          )}
        >
          {METHODS.map((m) => {
            const disabled = m.id === "local-browser" && !browserAvailable;
            return (
              <button
                key={m.id}
                onClick={() => onChange(m.id)}
                disabled={disabled}
                aria-disabled={disabled}
                title={disabled ? unavailableNote : undefined}
                className={cn(
                  "rounded px-2.5 py-1 text-xs font-medium transition-all",
                  value === m.id
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                  disabled &&
                    "cursor-not-allowed opacity-40 hover:text-muted-foreground",
                )}
              >
                {m.label}
                {disabled && (
                  <span className="ml-1 text-[10px] font-normal">
                    {browserInstalling ? "(downloading)" : "(not installed)"}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </TooltipTrigger>
      <TooltipContent side="bottom" className="max-w-xs">
        <p className="mb-1 font-semibold">Scrape method</p>
        {METHODS.map((m) => (
          <p key={m.id} className={cn("text-xs mt-1", value === m.id ? "text-foreground" : "text-muted-foreground")}>
            <span className="font-medium">{m.label}:</span> {m.description}
          </p>
        ))}
        {!browserAvailable && (
          <p className="mt-2 text-xs font-medium text-amber-600 dark:text-amber-400">
            {unavailableNote}
          </p>
        )}
      </TooltipContent>
    </Tooltip>
  );
}
