/**
 * The "choose your organization" prompt — the surfaced side of
 * `OrganizationNotSelectedError`. aidream refuses to guess an organization
 * for a caller, and this app refuses to invent one either, so when
 * resolution comes back empty the ONLY correct move is to ask the user.
 *
 * Mount ONCE near the app root. Any call site that hits
 * `OrganizationNotSelectedError` calls `requestOrganizationPicker()`
 * (dispatches `REQUEST_PICKER_EVENT`); this dialog listens for that event,
 * loads the user's real memberships via `listMemberOrganizations`, and lets
 * them pick — the picker itself never guesses either, it only lists actual
 * memberships from the canonical `mbr_for_user` RPC.
 */

import { useCallback, useEffect, useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import {
  REQUEST_PICKER_EVENT,
  listMemberOrganizations,
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
    return () => window.removeEventListener(REQUEST_PICKER_EVENT, handler);
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
            Every request needs to know which organization it acts in. Pick one to
            continue — you can change this later.
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
