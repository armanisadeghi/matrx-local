/**
 * AddCustomModelDialog — the ONE "add a custom checkpoint" flow (core/, so
 * every layout gets it from ModelPicker for free).
 *
 * One-paste UX: the user pastes a Hugging Face repo (id or URL) or a Civitai
 * model link, Inspect resolves it into a proposed entry (name, family,
 * format, size, warnings — rendered prominently, never hidden), Confirm
 * registers it and queues the weights download through the standard
 * DownloadManager streams.  The model then appears in the picker with a
 * "Custom" badge.
 *
 * Errors (unknown family refusal, unresolvable ref, 404 on old engine
 * builds) are rendered VERBATIM — the engine's reasons are user-facing.
 */

import { useState } from "react";
import {
  AlertTriangle,
  Download,
  KeyRound,
  Loader2,
  PackagePlus,
  Search,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import type { CustomImageModelInspectResult } from "@/lib/api";
import { ErrorNote, formatGb } from "@/components/media-gen/shared";

function ProposalBadge({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded bg-muted px-1.5 py-0.5 text-[10px]">
      {children}
    </span>
  );
}

export function AddCustomModelDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [, actions] = useMediaGenApp();
  const { inspectCustomModel, registerCustomModel } = actions;

  const [ref, setRef] = useState("");
  const [inspecting, setInspecting] = useState(false);
  const [registering, setRegistering] = useState(false);
  const [proposal, setProposal] =
    useState<CustomImageModelInspectResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const busy = inspecting || registering;

  const reset = () => {
    setRef("");
    setProposal(null);
    setError(null);
    setInspecting(false);
    setRegistering(false);
  };

  const handleInspect = async () => {
    const trimmed = ref.trim();
    if (!trimmed || busy) return;
    setInspecting(true);
    setError(null);
    setProposal(null);
    try {
      setProposal(await inspectCustomModel(trimmed));
    } catch (e) {
      // Verbatim — the engine's 400 reasons (unknown family, unsupported
      // format, unresolvable link…) are written for the user.
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setInspecting(false);
    }
  };

  const handleConfirm = async () => {
    if (!proposal || busy || !proposal.registerable) return;
    setRegistering(true);
    setError(null);
    try {
      // Round-trip the FULL entry from /inspect unchanged.
      await registerCustomModel(proposal.entry);
      onOpenChange(false);
      reset();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRegistering(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        onOpenChange(o);
        if (!o) reset();
      }}
    >
      <DialogContent className="flex max-h-[88vh] max-w-lg flex-col gap-0 overflow-hidden">
        <DialogHeader className="shrink-0">
          <DialogTitle>Add custom model</DialogTitle>
          <DialogDescription>
            Paste a Hugging Face repo (id or URL) or a Civitai model link.
            We&apos;ll check it and show what you&apos;re getting before
            anything downloads.
          </DialogDescription>
        </DialogHeader>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto py-1 pr-1">
          <div className="space-y-1.5">
            <Label className="text-xs">Hugging Face repo or Civitai link</Label>
            <div className="flex gap-2">
              <Input
                value={ref}
                onChange={(e) => setRef(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void handleInspect();
                }}
                placeholder="HF repo, or civitai.com / civitai.red link with ?modelVersionId=…"
                className="text-sm"
                disabled={busy}
              />
              <Button
                size="sm"
                disabled={!ref.trim() || busy}
                onClick={() => void handleInspect()}
              >
                {inspecting ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Search className="mr-1.5 h-3.5 w-3.5" />
                )}
                Inspect
              </Button>
            </div>
          </div>

          {error && <ErrorNote message={error} />}

          {proposal && (
            <div className="space-y-3 rounded-lg border bg-card p-4">
              <div>
                <p className="text-sm font-medium">{proposal.entry.name}</p>
                <p className="break-all text-[11px] text-muted-foreground">
                  {proposal.entry.source === "civitai"
                    ? "Civitai"
                    : "Hugging Face"}{" "}
                  · {proposal.entry.source_ref}
                </p>
              </div>
              <div className="flex flex-wrap gap-1.5">
                <span className="rounded bg-violet-500/15 px-1.5 py-0.5 text-[10px] font-medium text-violet-600 dark:text-violet-400">
                  {proposal.entry.family}
                </span>
                <ProposalBadge>
                  {proposal.entry.format === "diffusers"
                    ? "diffusers"
                    : "single file"}
                </ProposalBadge>
                <ProposalBadge>{proposal.entry.pipeline_type}</ProposalBadge>
                <ProposalBadge>
                  {formatGb(proposal.entry.size_gb)} download
                </ProposalBadge>
              </div>
              {proposal.entry.requires_hf_token && (
                <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/5 px-2.5 py-2 text-[11px] text-amber-600 dark:text-amber-400">
                  <KeyRound className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  This is a gated model — it needs your Hugging Face token
                  (Settings → API Keys).
                </div>
              )}
              {proposal.warnings.length > 0 && (
                <div className="space-y-1.5 rounded-md border border-amber-500/40 bg-amber-500/5 px-2.5 py-2">
                  {proposal.warnings.map((w) => (
                    <p
                      key={w}
                      className="flex items-start gap-2 text-[11px] leading-relaxed text-amber-600 dark:text-amber-400"
                    >
                      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      {w}
                    </p>
                  ))}
                </div>
              )}
              {!proposal.registerable && (
                <ErrorNote
                  message={
                    proposal.refusal_reason ??
                    "This model cannot be registered (the engine did not say why — that is a bug worth reporting)."
                  }
                />
              )}
              <div className="flex justify-end gap-2 pt-1">
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={registering}
                  onClick={() => {
                    setProposal(null);
                    setError(null);
                  }}
                >
                  Back
                </Button>
                <Button
                  size="sm"
                  disabled={registering || !proposal.registerable}
                  onClick={() => void handleConfirm()}
                >
                  {registering ? (
                    <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Download className="mr-1.5 h-3.5 w-3.5" />
                  )}
                  Add &amp; download ({formatGb(proposal.entry.size_gb)})
                </Button>
              </div>
            </div>
          )}

          {!proposal && !error && !inspecting && (
            <div className="flex items-start gap-2 rounded-md border border-dashed px-3 py-2.5 text-[11px] text-muted-foreground">
              <PackagePlus className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              Checkpoints from the community vary in quality and licensing —
              we&apos;ll surface any warnings here before you confirm.
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
