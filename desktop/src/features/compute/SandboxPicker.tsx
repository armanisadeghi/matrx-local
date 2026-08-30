/**
 * SandboxPicker — chat-header chip for binding this desktop's chats to a
 * compute target (sandbox or any of the user's matrx-local-registered PCs,
 * including THIS computer).
 *
 * Mirrors the matrx-extend SandboxPickerChip and matrx-frontend SandboxPanel
 * UX. The user's own PC is pinned at the top of "Your computers" labeled
 * "This computer" when this engine's instance_id matches an app_instances row.
 *
 * Selection persists via useComputeTargetStore (localStorage); resolved
 * server-side on every chat send via /api/compute-targets/resolve.
 */

import { useCallback, useEffect, useState } from "react";
import {
  Check,
  Cpu,
  Loader2,
  Monitor,
  Plus,
  RefreshCw,
  Server,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import supabase from "@/lib/supabase";
import {
  fetchComputeTargets,
  resolveConversationOrganizationId,
  type ComputeTarget,
  type ComputeTargetListResponse,
} from "@/lib/aidream-client";
import { cn } from "@/lib/utils";
import { useBoundComputeTarget } from "@/state/compute-target-store";

interface SandboxPickerProps {
  /** matrx-local's own `app_instances.instance_id` (from the cloud-sync debug
   * state). When a target's instance_id matches, it gets the "This computer"
   * label so the user can see which entry represents the machine they're on. */
  thisDeviceInstanceId: string | null;
}

export function SandboxPicker({ thisDeviceInstanceId }: SandboxPickerProps) {
  const [bound, setBound] = useBoundComputeTarget();

  const [data, setData] = useState<ComputeTargetListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  const refetch = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      const jwt = session?.access_token ?? null;
      // aidream refuses an authenticated request with no organization
      // before it routes — resolve it the same way conversation start does
      // whenever we're actually signed in.
      const organizationId = jwt
        ? await resolveConversationOrganizationId(jwt)
        : null;
      const resp = await fetchComputeTargets({ jwt, organizationId });
      setData(resp);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  // Lazy load — only fire when the popover opens.
  useEffect(() => {
    if (!open || data) return;
    void refetch();
  }, [open, data, refetch]);

  const sandboxes = (data?.targets ?? []).filter(
    (t) => t.kind === "ec2" || t.kind === "hosted",
  );
  const computers = (data?.targets ?? []).filter((t) => t.kind === "local-pc");
  // Pin "This computer" to the top of the computers list.
  computers.sort((a, b) => {
    const aThis = thisDeviceInstanceId && a.instance_id === thisDeviceInstanceId ? 0 : 1;
    const bThis = thisDeviceInstanceId && b.instance_id === thisDeviceInstanceId ? 0 : 1;
    return aThis - bThis;
  });
  const atSandboxLimit = data
    ? data.sandbox_count >= data.max_sandboxes
    : false;

  const TriggerIcon = bound
    ? bound.kind === "local-pc"
      ? Monitor
      : Server
    : Cpu;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className={cn(
            "h-7 gap-1.5 px-2 text-[11px] font-medium",
            bound
              ? "text-emerald-700 dark:text-emerald-400"
              : "text-muted-foreground",
          )}
          title={bound ? `Bound: ${bound.name}` : "Pick a sandbox or computer"}
        >
          <TriggerIcon className="h-3.5 w-3.5" />
          <span className="hidden max-w-[120px] truncate sm:inline">
            {bound?.name ?? "Compute"}
          </span>
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-80 p-0" align="end">
        <div className="flex items-center justify-between border-b px-3 py-2">
          <div>
            <div className="text-xs font-semibold">Agent compute target</div>
            <div className="text-[10px] text-muted-foreground">
              Where shell &amp; file tools run for this chat.
            </div>
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6 text-muted-foreground"
            title="Refresh"
            onClick={() => void refetch()}
            disabled={loading}
          >
            <RefreshCw className={cn("h-3 w-3", loading && "animate-spin")} />
          </Button>
        </div>

        {bound && (
          <div className="border-b bg-accent/40 px-3 py-2">
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate text-[11px] font-semibold">
                  {bound.name}
                </div>
                <div className="text-[10px] text-muted-foreground">
                  Currently bound
                </div>
              </div>
              <Button
                variant="ghost"
                size="sm"
                className="h-7 gap-1 px-2 text-[10px] text-destructive hover:bg-destructive/10"
                onClick={() => setBound(null)}
              >
                <X className="h-3 w-3" />
                Detach
              </Button>
            </div>
          </div>
        )}

        {error && (
          <div className="border-b px-3 py-2 text-[11px] text-destructive">
            Could not load targets: {error}
          </div>
        )}

        {loading && !data && (
          <div className="flex items-center gap-2 px-3 py-3 text-[11px] text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            Loading…
          </div>
        )}

        {data && (
          <div className="max-h-72 overflow-y-auto">
            <SectionHeader>Your computers</SectionHeader>
            {computers.length === 0 && (
              <EmptyHint>
                No matrx-local devices online. Sign in &amp; let this engine
                register before binding.
              </EmptyHint>
            )}
            {computers.map((target) => {
              const isThis =
                thisDeviceInstanceId !== null &&
                target.instance_id === thisDeviceInstanceId;
              return (
                <TargetRow
                  key={target.id}
                  target={target}
                  thisDevice={isThis}
                  bound={
                    bound?.kind === target.kind && bound.rowId === target.id
                  }
                  onSelect={() => {
                    setBound({
                      kind: target.kind,
                      rowId: target.id,
                      name: isThis ? `${target.name} (this computer)` : target.name,
                    });
                    setOpen(false);
                  }}
                />
              );
            })}

            <SectionHeader>Sandboxes</SectionHeader>
            {sandboxes.length === 0 && (
              <EmptyHint>
                No sandboxes yet. Your plan allows {data.max_sandboxes}.
              </EmptyHint>
            )}
            {sandboxes.map((target) => (
              <TargetRow
                key={target.id}
                target={target}
                thisDevice={false}
                bound={bound?.kind === target.kind && bound.rowId === target.id}
                onSelect={() => {
                  setBound({
                    kind: target.kind,
                    rowId: target.id,
                    name: target.name,
                  });
                  setOpen(false);
                }}
              />
            ))}

            <div className="border-t bg-muted/30 px-2 py-2">
              <Button
                variant="ghost"
                size="sm"
                className="h-7 w-full justify-start gap-1.5 px-2 text-[11px] text-muted-foreground"
                disabled={atSandboxLimit}
                title={
                  atSandboxLimit
                    ? `At plan limit (${data.max_sandboxes}). Upgrade to add more.`
                    : "Create a new sandbox on aimatrx.com"
                }
                onClick={() => {
                  void window.open("https://aimatrx.com/sandboxes/new", "_blank");
                }}
              >
                <Plus className="h-3 w-3" />
                {atSandboxLimit
                  ? `Upgrade (${data.sandbox_count}/${data.max_sandboxes} used)`
                  : "New sandbox"}
              </Button>
            </div>
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
      {children}
    </div>
  );
}

function EmptyHint({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-3 pb-2 text-[10px] text-muted-foreground">
      {children}
    </div>
  );
}

function TargetRow({
  target,
  thisDevice,
  bound,
  onSelect,
}: {
  target: ComputeTarget;
  thisDevice: boolean;
  bound: boolean;
  onSelect: () => void;
}) {
  const Icon = target.kind === "local-pc" ? Monitor : Server;
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        "flex w-full items-center gap-2 px-3 py-1.5 text-left text-[11px] hover:bg-accent",
        bound && "bg-accent/60",
      )}
    >
      <span
        className={cn(
          "inline-block h-1.5 w-1.5 shrink-0 rounded-full",
          target.is_online ? "bg-emerald-500" : "bg-muted-foreground/40",
        )}
        title={target.is_online ? "Online" : "Offline"}
      />
      <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      <span className="min-w-0 flex-1 truncate">
        {target.name}
        {thisDevice && (
          <span className="ml-1 rounded bg-primary/20 px-1 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-primary">
            This computer
          </span>
        )}
      </span>
      <span className="shrink-0 text-[9px] uppercase tracking-wide text-muted-foreground">
        {target.status}
      </span>
      {bound && <Check className="h-3 w-3 shrink-0 text-emerald-600" />}
    </button>
  );
}
