/**
 * The "choose your organization" prompt for a request that is WAITING — the
 * surfaced side of `OrganizationNotSelectedError`. aidream refuses to guess an
 * organization for a caller, and this app refuses to invent one either, so
 * when resolution comes back empty the ONLY correct move is to ask the user.
 *
 * Same store, same list, same write as the top-bar `OrganizationSwitcher`:
 * this is that control in a modal, raised by `requireActiveOrganizationId()`
 * (via `requestOrganizationPicker()` / `REQUEST_PICKER_EVENT`), which then
 * WAITS — the held request proceeds the moment the user picks here.
 *
 * Mount ONCE near the app root. It also owns the two ways THE ENGINE's copy
 * of this Mac's answer is kept equal to this store's:
 *
 *   - every time the engine becomes reachable, the store's value is re-stated
 *     to it (retried until it takes it);
 *   - when the engine publishes its own `organization_required` ask (its copy
 *     is empty — a fresh engine, a reinstall, a `--fresh` dev home) while this
 *     device already has an answer, the answer is re-sent and the person is
 *     NOT asked again. Only when this device has no answer either does the
 *     picker open.
 */

import { useCallback, useEffect, useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@ai-matrx/design-system";
import { registerActionNeededHandler, useActionNeeded } from "@/features/action-needed";
import { getAppRuntimeConfig } from "@/lib/app-config";
import { openExternal } from "@/lib/open-external";
import {
  REQUEST_PICKER_EVENT,
  getActiveOrganizationSnapshot,
  republishActiveOrganizationToEngine,
  requestOrganizationPicker,
} from "@/lib/org/active-org";
import { useActiveOrganization } from "@/lib/org/use-active-organization";

/** The engine's own ask (`app/services/action_needed/models.py`). */
export const ENGINE_ORGANIZATION_ASK_FINGERPRINT = "organization:required";

export interface OrganizationPickerDialogProps {
  /**
   * The engine's own connection state. The sidecar keeps a SEPARATE copy of
   * this Mac's pick and cannot read this window's `localStorage`, so the
   * answer has to be re-stated to it every time it becomes reachable — see
   * `republishActiveOrganizationToEngine`.
   */
  engineStatus?: string;
}

export function OrganizationPickerDialog({ engineStatus }: OrganizationPickerDialogProps = {}) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savingId, setSavingId] = useState<string | null>(null);
  const { organization, organizations, loading, error: loadError, userId, choose, reload } =
    useActiveOrganization({ load: false });
  const actionNeeded = useActionNeeded();

  useEffect(() => {
    const handler = () => {
      setOpen(true);
      setError(null);
      void reload();
    };
    window.addEventListener(REQUEST_PICKER_EVENT, handler);
    // The sidecar's own ask. It cannot open a dialog, so it publishes an
    // `organization_required` action-needed item. Acting on that item answers
    // it from THIS store when there is an answer, and only otherwise asks.
    const unregister = registerActionNeededHandler("choose_organization", async () => {
      if (getActiveOrganizationSnapshot().organization) {
        const accepted = await republishActiveOrganizationToEngine();
        if (accepted) return;
      }
      requestOrganizationPicker();
    });
    return () => {
      window.removeEventListener(REQUEST_PICKER_EVENT, handler);
      unregister();
    };
  }, [reload]);

  // THE ENGINE ASKED A QUESTION THIS DEVICE ALREADY ANSWERED. Its card is
  // the "separate warning" a person sees while the top bar shows an
  // organization — the engine's copy lagged (push failed while it was
  // booting, a fresh engine home). Answer it from the store the moment it
  // appears; the engine withdraws the card when it takes the value.
  const engineAsking = actionNeeded.some(
    (item) => item.fingerprint === ENGINE_ORGANIZATION_ASK_FINGERPRINT,
  );
  useEffect(() => {
    if (!engineAsking || !organization) return;
    void republishActiveOrganizationToEngine();
  }, [engineAsking, organization]);

  // TELL THE ENGINE WHAT THIS MAC ALREADY ANSWERED — every time it is
  // reachable, and keep trying until it takes it.
  //
  // The engine boots alongside (usually after) this window, so a single push
  // at mount lands on nobody: the PUT fails, the engine stays empty, and the
  // next background job HOLDS and publishes `organization_required` — asking
  // the person a question they answered on this Mac days ago. Driving it off
  // the engine's connection state, with a retry, is what makes the headless
  // half of the app inherit the answer instead of re-asking for it.
  //
  // Also re-run on a sign-in / account switch: the engine's copy is scoped to
  // the user who set it, so the returning account's answer must be re-sent.
  useEffect(() => {
    if (engineStatus !== undefined && engineStatus !== "connected") return;
    if (!userId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const attempt = (delayMs: number) => {
      void republishActiveOrganizationToEngine().then((accepted) => {
        if (cancelled || accepted) return;
        // Not up, or not signed in yet. Back off, but never give up silently:
        // giving up is what produced the double-ask.
        timer = setTimeout(() => attempt(Math.min(delayMs * 2, 30_000)), delayMs);
      });
    };
    attempt(2_000);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [engineStatus, userId]);

  const pick = useCallback(
    async (organizationId: string) => {
      setSavingId(organizationId);
      setError(null);
      try {
        await choose(organizationId);
        setOpen(false);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not select that organization.");
      } finally {
        setSavingId(null);
      }
    },
    [choose],
  );

  const shownError = error ?? loadError;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Choose your organization</DialogTitle>
          <DialogDescription>
            Every request needs to know which organization it acts in, and
            nothing picks one for you. Choose one and whatever was waiting will
            continue. You can change it any time from the top bar.
          </DialogDescription>
        </DialogHeader>
        {shownError && <p className="text-sm text-destructive">{shownError}</p>}
        {organizations === null && loading ? (
          <p className="text-sm text-muted-foreground">Loading your organizations…</p>
        ) : organizations && organizations.length === 0 && !shownError ? (
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              You don&apos;t belong to any organization yet, so there is nothing to pick. Any
              request that needed one has already stopped waiting — create an organization or ask
              whoever runs your workspace to invite you, then try again.
            </p>
            <Button
              size="sm"
              variant="outline"
              onClick={() =>
                void openExternal(`${getAppRuntimeConfig().webAppOrigin}/organizations`)
              }
            >
              Create or join an organization
            </Button>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            {(organizations ?? []).map((org) => (
              <Button
                key={org.id}
                variant={org.id === organization?.id ? "default" : "outline"}
                className="justify-start"
                disabled={savingId !== null}
                onClick={() => void pick(org.id)}
              >
                {savingId === org.id ? "Selecting…" : org.name}
                {org.isPersonal ? " (personal)" : ""}
              </Button>
            ))}
            {shownError && organizations === null && (
              <Button size="sm" variant="outline" onClick={() => void reload()}>
                Try again
              </Button>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
