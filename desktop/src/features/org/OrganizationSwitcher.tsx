/**
 * THE organization selector — the one control that shows and changes the
 * organization this device acts in. Mounted once, in the top bar.
 *
 * It renders THE state value (`useActiveOrganization`) and writes through THE
 * one write (`choose` -> `setActiveOrganization`), which persists the choice,
 * mirrors it to the engine, and — through the engine — to the server's
 * coding-session filing organization. Nothing else in the app has its own
 * idea of the organization: every page banner that says "choose an
 * organization" opens the same list (`OrganizationPickerDialog`, which is
 * this control's list in a modal for a request that is waiting).
 *
 * The button always tells the truth: the current organization's name when
 * one is set, "Choose organization" in attention colors when none is — a
 * screen never shows a chosen organization it is not actually using.
 */

import { useState } from "react";
import { Building2, Check, ChevronDown, Loader2, RefreshCw } from "lucide-react";
import {
  Button,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@ai-matrx/design-system";

import { getAppRuntimeConfig } from "@/lib/app-config";
import { openExternal } from "@/lib/open-external";
import { useActiveOrganization } from "@/lib/org/use-active-organization";
import { cn } from "@/lib/utils";

export interface OrganizationSwitcherProps {
  /** Compact (icon + name, top-bar sized) or a full-width row. */
  variant?: "bar" | "row";
  className?: string;
}

export function OrganizationSwitcher({ variant = "bar", className }: OrganizationSwitcherProps) {
  const { organization, organizations, loading, error, userId, choose, reload } =
    useActiveOrganization();
  const [savingId, setSavingId] = useState<string | null>(null);
  const [chooseError, setChooseError] = useState<string | null>(null);

  if (!userId) return null;

  const pick = async (id: string) => {
    if (id === organization?.id) return;
    setSavingId(id);
    setChooseError(null);
    try {
      await choose(id);
    } catch (err) {
      setChooseError(err instanceof Error ? err.message : "Could not select that organization.");
    } finally {
      setSavingId(null);
    }
  };

  const label = organization ? organization.name : "Choose organization";
  const attention = !organization;

  return (
    <DropdownMenu>
      <Tooltip delayDuration={150}>
        <TooltipTrigger asChild>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              data-testid="organization-switcher"
              data-organization-id={organization?.id ?? ""}
              aria-label={
                organization
                  ? `Organization: ${organization.name}. Change organization`
                  : "Choose organization"
              }
              className={cn(
                "flex items-center gap-1.5 rounded-md border text-sm transition-colors",
                variant === "bar" ? "h-7 max-w-56 px-2" : "h-9 w-full px-3",
                attention
                  ? "border-amber-500/60 bg-amber-500/10 text-amber-700 hover:bg-amber-500/20 dark:text-amber-300"
                  : "border-border text-foreground hover:bg-muted/50",
                className,
              )}
            >
              <Building2 className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate">{label}</span>
              {savingId ? (
                <Loader2 className="h-3 w-3 shrink-0 animate-spin" />
              ) : (
                <ChevronDown className="h-3 w-3 shrink-0 opacity-60" />
              )}
            </button>
          </DropdownMenuTrigger>
        </TooltipTrigger>
        <TooltipContent side="bottom">
          {organization
            ? `Working in ${organization.name}. Everything this app does is filed here.`
            : "Nothing has chosen an organization on this Mac yet. Choose one — nothing picks for you."}
        </TooltipContent>
      </Tooltip>
      <DropdownMenuContent align="start" className="w-64 max-h-[70vh] overflow-y-auto">
        <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
          Organization for this Mac
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        {loading && organizations === null ? (
          <div className="px-2 py-1.5 text-sm text-muted-foreground">Loading your organizations…</div>
        ) : error && (organizations === null || organizations.length === 0) ? (
          <div className="space-y-2 px-2 py-1.5 text-sm">
            <p className="text-destructive">{error}</p>
            <Button size="sm" variant="outline" onClick={() => void reload()}>
              <RefreshCw className="mr-1.5 h-3.5 w-3.5" /> Try again
            </Button>
          </div>
        ) : organizations && organizations.length === 0 ? (
          <div className="space-y-2 px-2 py-1.5 text-sm">
            <p className="text-muted-foreground">
              You don&apos;t belong to any organization yet, so there is nothing to choose.
            </p>
            <Button
              size="sm"
              variant="outline"
              onClick={() => void openExternal(`${getAppRuntimeConfig().webAppOrigin}/organizations`)}
            >
              Create or join an organization
            </Button>
          </div>
        ) : (
          (organizations ?? []).map((org) => (
            <DropdownMenuItem
              key={org.id}
              data-organization-id={org.id}
              disabled={savingId !== null}
              onSelect={() => void pick(org.id)}
              className="flex items-center gap-2"
            >
              <span className="flex h-4 w-4 shrink-0 items-center justify-center">
                {savingId === org.id ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : org.id === organization?.id ? (
                  <Check className="h-3.5 w-3.5" />
                ) : null}
              </span>
              <span className="truncate">{org.name}</span>
              {org.isPersonal && (
                <span className="ml-auto text-xs text-muted-foreground">personal</span>
              )}
            </DropdownMenuItem>
          ))
        )}
        {chooseError && (
          <>
            <DropdownMenuSeparator />
            <p className="px-2 py-1.5 text-xs text-destructive">{chooseError}</p>
          </>
        )}
        {organizations && organizations.length > 0 && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={(event) => { event.preventDefault(); void reload(); }} className="text-xs text-muted-foreground">
              <RefreshCw className={cn("mr-2 h-3 w-3", loading && "animate-spin")} /> Refresh list
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
