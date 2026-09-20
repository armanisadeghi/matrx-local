/**
 * The "choose your organization" prompt — the surfaced side of
 * `OrganizationNotSelectedError`. aidream refuses to guess an organization
 * for a caller, and this app refuses to invent one either, so when
 * resolution comes back empty the ONLY correct move is to ask the user.
 *
 * Mount ONCE near the app root. `requireActiveOrganizationId()` raises this
 * dialog (via `requestOrganizationPicker()` / `REQUEST_PICKER_EVENT`) and
 * then WAITS: the request that needed an organization is held open, and it
 * proceeds the moment the user picks here. The Python sidecar reaches the
 * same dialog through its `organization_required` action-needed item, whose
 * `choose_organization` handler is registered below.
 *
 * It loads the user's real memberships via `listMemberOrganizations` — the
 * picker never guesses either, it only lists actual memberships from the
 * canonical `mbr_for_user` RPC. There is no "default" to offer and no
 * pre-selected row: the user SETS one (Arman, 2026-09-19).
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
import { registerActionNeededHandler } from "@/features/action-needed/actions";
import {
  REQUEST_PICKER_EVENT,
  listMemberOrganizations,
  republishActiveOrganizationToEngine,
  requestOrganizationPicker,
  setActiveOrganization,
  type MemberOrganization,
} from "@/lib/org/active-org";

export function OrganizationPickerDialog() {
  const [open, setOpen] = useState(false);
  const [organizations, setOrganizations] = useState<MemberOrganization[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savingId, setSavingId] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    setOrganizations(null);
    listMemberOrganizations()
      .then(setOrganizations)
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Could not load your organizations.");
        setOrganizations([]);
      });
  }, []);

  useEffect(() => {
    const handler = () => {
      setOpen(true);
      load();
    };
    window.addEventListener(REQUEST_PICKER_EVENT, handler);
    // The sidecar's own ask. It cannot open a dialog, so it publishes an
    // `organization_required` action-needed item; acting on that item is what
    // brings this picker up, and the held background work retries with the
    // value the user sets.
    const unregister = registerActionNeededHandler("choose_organization", () =>
      requestOrganizationPicker(),
    );
    // The engine keeps its own copy of this Mac's pick, and a fresh engine
    // (reinstall, `dev.sh --fresh`) starts with none. Re-state what this
    // window already knows so background work is not held on a question the
    // user already answered.
    void republishActiveOrganizationToEngine();
    return () => {
      window.removeEventListener(REQUEST_PICKER_EVENT, handler);
      unregister();
    };
  }, [load]);

  const choose = useCallback(async (organizationId: string) => {
    setSavingId(organizationId);
    try {
      await setActiveOrganization(organizationId);
      setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not select that organization.");
    } finally {
      setSavingId(null);
    }
  }, []);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Choose your organization</DialogTitle>
          <DialogDescription>
            Every request needs to know which organization it acts in, and
            nothing picks one for you. Choose one and whatever was waiting will
            continue. You can change it whenever you like.
          </DialogDescription>
        </DialogHeader>
        {error && <p className="text-sm text-destructive">{error}</p>}
        {organizations === null && !error ? (
          <p className="text-sm text-muted-foreground">Loading your organizations…</p>
        ) : organizations && organizations.length === 0 && !error ? (
          <p className="text-sm text-muted-foreground">
            You don&apos;t belong to any organization yet.
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            {(organizations ?? []).map((org) => (
              <Button
                key={org.id}
                variant="outline"
                className="justify-start"
                disabled={savingId !== null}
                onClick={() => void choose(org.id)}
              >
                {savingId === org.id ? "Selecting…" : org.name}
                {org.isPersonal ? " (personal)" : ""}
              </Button>
            ))}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
