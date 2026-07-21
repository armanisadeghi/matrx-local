import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AlertCircle,
  Download,
  KeyRound,
  Loader2,
  Search,
  Settings2,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useDownloadManager } from "@/contexts/DownloadManagerContext";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import type { SelectedLora } from "@/hooks/use-media-gen";
import type { ImageGenLoraInfo } from "@/lib/api";
import {
  classifyLoraCompatibility,
  loraMatchesSearch,
  loraVisibleForModel,
  modelLoraFamily,
} from "@/lib/image-gen/lora-compatibility";
import { ErrorNote, formatGb } from "@/components/media-gen/shared";
import type { ImageGenController } from "./imageController";

const PAGE_SIZE = 75;

export function updateLoraSelection(
  selections: SelectedLora[],
  id: string,
  patch: Partial<SelectedLora>,
): SelectedLora[] {
  const existing = selections.find((selection) => selection.id === id);
  const next: SelectedLora = {
    id,
    scale: existing?.scale ?? 1,
    enabled: existing?.enabled ?? false,
    ...patch,
  };
  return [...selections.filter((selection) => selection.id !== id), next];
}

function FamilyBadge({
  lora,
  ctl,
}: {
  lora: ImageGenLoraInfo;
  ctl: ImageGenController;
}) {
  const compatibility = classifyLoraCompatibility(lora.base_family, ctl.model);
  return (
    <Badge
      variant="outline"
      className={
        compatibility === "incompatible"
          ? "border-amber-500/50 text-amber-600 dark:text-amber-400"
          : "text-muted-foreground"
      }
    >
      {lora.base_family || "unknown"}
    </Badge>
  );
}

function InstalledLoraRow({
  lora,
  ctl,
}: {
  lora: ImageGenLoraInfo;
  ctl: ImageGenController;
}) {
  const [, actions] = useMediaGenApp();
  const selected = ctl.form.loras.find((selection) => selection.id === lora.id);
  const enabled = selected?.enabled ?? false;
  const scale = selected?.scale ?? 1;
  const compatibility = classifyLoraCompatibility(lora.base_family, ctl.model);
  const isInstalled = lora.installed !== false;
  const cannotEnable =
    !isInstalled || (compatibility === "incompatible" && !enabled);

  const setSelection = (patch: Partial<SelectedLora>) => {
    actions.setImageForm({
      loras: updateLoraSelection(ctl.form.loras, lora.id, patch),
    });
  };

  return (
    <div className="space-y-2 rounded-lg border px-3 py-2.5">
      <div className="flex items-start gap-2">
        <Checkbox
          checked={enabled}
          disabled={cannotEnable}
          onCheckedChange={(checked) =>
            setSelection({ enabled: checked === true })
          }
          aria-label={`${enabled ? "Disable" : "Enable"} ${lora.name || lora.id}`}
          className="mt-0.5 h-3.5 w-3.5"
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <p
              className="min-w-0 truncate text-xs font-medium"
              title={lora.name ?? lora.repo_id}
            >
              {lora.name || lora.id}
            </p>
            <FamilyBadge lora={lora} ctl={ctl} />
            {!lora.installed && <Badge variant="outline">Downloading</Badge>}
          </div>
          <p className="truncate text-[10px] text-muted-foreground">
            {lora.repo_id}
            {lora.size_bytes > 0
              ? ` · ${formatGb(lora.size_bytes / 1024 ** 3)}`
              : ""}
            {lora.source ? ` · ${lora.source}` : ""}
          </p>
          {compatibility === "incompatible" && (
            <p className="mt-1 flex items-start gap-1 text-[10px] text-amber-600 dark:text-amber-400">
              <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
              Built for {lora.base_family}; activation is disabled for{" "}
              {ctl.model?.name ?? "this model"}.
            </p>
          )}
          {compatibility === "unknown" && (
            <p className="mt-1 text-[10px] text-muted-foreground">
              Family unclassified — the engine will attempt it and report any
              real incompatibility.
            </p>
          )}
          {!lora.installed && (
            <p className="mt-1 text-[10px] text-muted-foreground">
              Activation becomes available after the download passes integrity
              checks.
            </p>
          )}
        </div>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="h-7 w-7 shrink-0 text-muted-foreground hover:text-destructive"
          onClick={() => {
            if (enabled) setSelection({ enabled: false });
            void actions.deleteLora(lora.id);
          }}
          aria-label={`Delete ${lora.name || lora.id} from disk`}
          title="Delete from this device"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </Button>
      </div>
      {enabled && (
        <div className="flex items-center gap-2 pl-5">
          <span className="w-14 shrink-0 text-[10px] text-muted-foreground">
            Strength
          </span>
          <Slider
            min={0}
            max={1.5}
            step={0.05}
            value={[scale]}
            onValueChange={([value]) =>
              value !== undefined && setSelection({ scale: value })
            }
            className="flex-1"
          />
          <span className="w-9 text-right text-[10px] tabular-nums text-muted-foreground">
            {scale.toFixed(2)}
          </span>
        </div>
      )}
    </div>
  );
}

function AvailableLoraRow({
  lora,
  ctl,
  download,
  onDownload,
}: {
  lora: ImageGenLoraInfo;
  ctl: ImageGenController;
  download: { status: string; percent: number } | undefined;
  onDownload: () => void;
}) {
  const downloading =
    download?.status === "active" || download?.status === "queued";
  const compatibility = classifyLoraCompatibility(lora.base_family, ctl.model);
  return (
    <div className="space-y-2 rounded-lg border px-3 py-2.5">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <p className="min-w-0 truncate text-xs font-medium">
              {lora.name || lora.repo_id}
            </p>
            <FamilyBadge lora={lora} ctl={ctl} />
            {lora.unverified && <Badge variant="outline">Unverified</Badge>}
          </div>
          <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
            {lora.description}
          </p>
          <p className="mt-1 truncate text-[10px] text-muted-foreground/80">
            {lora.repo_id}
            {lora.source ? ` · ${lora.source}` : ""}
            {lora.license ? ` · ${lora.license}` : ""}
          </p>
          {compatibility === "incompatible" && (
            <p className="mt-1 text-[10px] text-amber-600 dark:text-amber-400">
              You can download and manage this family, but it cannot be
              activated for the selected model.
            </p>
          )}
        </div>
        {downloading ? (
          <span className="flex shrink-0 items-center text-[11px] tabular-nums text-violet-500">
            <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            {Math.round(download.percent)}%
          </span>
        ) : (
          <Button
            size="sm"
            variant="outline"
            className="h-7 shrink-0 px-2.5 text-xs"
            onClick={onDownload}
          >
            <Download className="mr-1 h-3 w-3" />
            Get
          </Button>
        )}
      </div>
      {downloading && (
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted/60">
          <div
            className="h-full rounded-full bg-violet-500 transition-[width] duration-300"
            style={{
              width: `${Math.min(100, Math.max(0, download.percent))}%`,
            }}
          />
        </div>
      )}
    </div>
  );
}

function LoraManagerDialog({
  open,
  onOpenChange,
  ctl,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  ctl: ImageGenController;
}) {
  const [state, actions] = useMediaGenApp();
  const { downloads } = useDownloadManager();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [showAllFamilies, setShowAllFamilies] = useState(false);
  const [tab, setTab] = useState("installed");
  const [limit, setLimit] = useState(PAGE_SIZE);
  const [repoInput, setRepoInput] = useState("");

  const installed = state.loraList?.installed ?? [];
  const catalog = state.loraList?.catalog ?? [];
  const activeIds = new Set(
    ctl.form.loras
      .filter((selection) => selection.enabled)
      .map((selection) => selection.id),
  );
  const installedRepoIds = new Set(installed.map((lora) => lora.repo_id));
  const activeInstalled = installed.filter((lora) => activeIds.has(lora.id));
  const missingActive = ctl.form.loras.filter(
    (selection) =>
      selection.enabled && !installed.some((lora) => lora.id === selection.id),
  );

  const entryByRepo = useMemo(() => {
    const entries: Record<string, (typeof downloads)[number]> = {};
    for (const [repoId, downloadId] of Object.entries(state.loraDownloads)) {
      const entry = downloads.find((candidate) => candidate.id === downloadId);
      if (entry) entries[repoId] = entry;
    }
    return entries;
  }, [downloads, state.loraDownloads]);

  const completedTracked = useMemo(
    () =>
      Object.values(entryByRepo).filter((entry) => entry.status === "completed")
        .length,
    [entryByRepo],
  );
  useEffect(() => {
    if (completedTracked > 0) void actions.refreshLoras();
  }, [completedTracked, actions.refreshLoras]);

  useEffect(() => {
    setShowAllFamilies(false);
  }, [ctl.model?.model_id]);

  useEffect(() => {
    setLimit(PAGE_SIZE);
  }, [query, showAllFamilies, tab, ctl.model?.model_id]);

  const visibleInstalled = installed.filter(
    (lora) =>
      !activeIds.has(lora.id) &&
      loraMatchesSearch(lora, query) &&
      loraVisibleForModel(lora, ctl.model, showAllFamilies),
  );
  const visibleAvailable = catalog.filter(
    (lora) =>
      !lora.installed &&
      !installedRepoIds.has(lora.repo_id) &&
      loraMatchesSearch(lora, query) &&
      loraVisibleForModel(lora, ctl.model, showAllFamilies),
  );
  const currentFamily = modelLoraFamily(ctl.model);

  const submitCustom = () => {
    const ref = repoInput.trim();
    if (!ref) return;
    void actions.downloadLora(ref);
    setRepoInput("");
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[88vh] w-[min(96vw,48rem)] max-w-3xl flex-col overflow-hidden p-0">
        <DialogHeader className="border-b px-5 pb-4 pt-5">
          <DialogTitle>Manage LoRA styles</DialogTitle>
          <DialogDescription>
            Search, install, activate, and tune adapters. Results default to{" "}
            {ctl.model?.name ?? "the selected model"}.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 px-5 pt-4">
          {state.loraError && (
            <ErrorNote
              message={`LoRA styles unavailable — ${state.loraError}`}
            />
          )}
          {state.loraNeedsCivitaiKey && (
            <div className="flex items-center justify-between gap-3 rounded-lg border border-amber-500/40 bg-amber-500/5 px-3 py-2.5">
              <p className="flex items-start gap-2 text-[11px] text-amber-600 dark:text-amber-400">
                <KeyRound className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                Civitai downloads need your Civitai API key.
              </p>
              <Button
                size="sm"
                variant="outline"
                className="h-7 text-xs"
                onClick={() => navigate("/settings?tab=api-keys")}
              >
                Set key
              </Button>
            </div>
          )}
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search names, descriptions, sources, or families…"
              className="pl-9"
            />
          </div>
          <div className="flex items-center justify-between gap-3 rounded-md bg-muted/40 px-3 py-2">
            <div>
              <Label htmlFor="show-all-lora-families" className="text-xs">
                Show all families
              </Label>
              <p className="text-[10px] text-muted-foreground">
                Default filter: {currentFamily}. Incompatible entries remain
                management-only.
              </p>
            </div>
            <Switch
              id="show-all-lora-families"
              checked={showAllFamilies}
              onCheckedChange={setShowAllFamilies}
            />
          </div>
          {(activeInstalled.length > 0 || missingActive.length > 0) && (
            <div className="space-y-1.5">
              <p className="text-[11px] font-medium text-muted-foreground">
                Active ({activeInstalled.length + missingActive.length})
              </p>
              {missingActive.length > 0 && (
                <ErrorNote
                  message={`Active style${missingActive.length === 1 ? "" : "s"} missing from disk: ${missingActive.map((selection) => selection.id).join(", ")}. Remove or reinstall before generating.`}
                />
              )}
              {activeInstalled.length > 0 && (
                <ScrollArea className="max-h-36 pr-3">
                  <div className="space-y-2">
                    {activeInstalled.map((lora) => (
                      <InstalledLoraRow key={lora.id} lora={lora} ctl={ctl} />
                    ))}
                  </div>
                </ScrollArea>
              )}
            </div>
          )}
        </div>

        <div className="min-h-0 flex-1 px-5 pb-4 pt-3">
          <Tabs
            value={tab}
            onValueChange={setTab}
            className="flex h-full min-h-0 flex-col"
          >
            <TabsList className="grid w-full grid-cols-2">
              <TabsTrigger value="installed" className="text-xs">
                Installed ({visibleInstalled.length})
              </TabsTrigger>
              <TabsTrigger value="available" className="text-xs">
                Available ({visibleAvailable.length})
              </TabsTrigger>
            </TabsList>
            <TabsContent value="installed" className="min-h-0 flex-1">
              <ScrollArea className="h-full max-h-[42vh] pr-3">
                <div className="space-y-2 pb-1">
                  {visibleInstalled.slice(0, limit).map((lora) => (
                    <InstalledLoraRow key={lora.id} lora={lora} ctl={ctl} />
                  ))}
                  {visibleInstalled.length === 0 && (
                    <p className="rounded-md border border-dashed px-3 py-6 text-center text-xs text-muted-foreground">
                      No installed LoRAs match this model and search.
                    </p>
                  )}
                  {visibleInstalled.length > limit && (
                    <Button
                      variant="outline"
                      className="w-full"
                      onClick={() => setLimit((value) => value + PAGE_SIZE)}
                    >
                      Show{" "}
                      {Math.min(PAGE_SIZE, visibleInstalled.length - limit)}{" "}
                      more
                    </Button>
                  )}
                </div>
              </ScrollArea>
            </TabsContent>
            <TabsContent value="available" className="min-h-0 flex-1">
              <ScrollArea className="h-full max-h-[42vh] pr-3">
                <div className="space-y-2 pb-1">
                  {visibleAvailable.slice(0, limit).map((lora) => (
                    <AvailableLoraRow
                      key={lora.repo_id}
                      lora={lora}
                      ctl={ctl}
                      download={entryByRepo[lora.repo_id]}
                      onDownload={() =>
                        void actions.downloadLora(
                          lora.repo_id,
                          lora.weight_name ?? undefined,
                        )
                      }
                    />
                  ))}
                  {visibleAvailable.length === 0 && (
                    <p className="rounded-md border border-dashed px-3 py-6 text-center text-xs text-muted-foreground">
                      No available LoRAs match this model and search.
                    </p>
                  )}
                  {visibleAvailable.length > limit && (
                    <Button
                      variant="outline"
                      className="w-full"
                      onClick={() => setLimit((value) => value + PAGE_SIZE)}
                    >
                      Show{" "}
                      {Math.min(PAGE_SIZE, visibleAvailable.length - limit)}{" "}
                      more
                    </Button>
                  )}
                </div>
              </ScrollArea>
            </TabsContent>
          </Tabs>
        </div>

        <div className="border-t px-5 py-4">
          <Label className="text-xs">
            Install from Hugging Face or Civitai
          </Label>
          <div className="mt-1.5 flex gap-2">
            <Input
              value={repoInput}
              onChange={(event) => setRepoInput(event.target.value)}
              onKeyDown={(event) => event.key === "Enter" && submitCustom()}
              placeholder="Repo, model id, or model/version link"
            />
            <Button
              size="sm"
              disabled={!repoInput.trim()}
              onClick={submitCustom}
            >
              <Download className="mr-1.5 h-3.5 w-3.5" />
              Install
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** Compact page summary; the potentially huge library lives in the manager. */
export function LoraStylesSection({ ctl }: { ctl: ImageGenController }) {
  const [state, actions] = useMediaGenApp();
  const [managerOpen, setManagerOpen] = useState(false);
  const installedById = new Map(
    (state.loraList?.installed ?? []).map((lora) => [lora.id, lora]),
  );
  const active = ctl.form.loras.filter((selection) => selection.enabled);
  const missingActive = active.filter(
    (selection) => !installedById.has(selection.id),
  );
  const disabledForModel = ctl.form.loras.filter((selection) => {
    if (selection.enabled) return false;
    const installed = installedById.get(selection.id);
    return (
      classifyLoraCompatibility(installed?.base_family, ctl.model) ===
      "incompatible"
    );
  });

  return (
    <div className="space-y-2 rounded-lg border px-3 py-2.5">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-xs font-medium">
            <Sparkles className="h-3.5 w-3.5 text-violet-500" />
            LoRA styles
            {active.length > 0 && (
              <Badge variant="secondary" className="ml-1">
                {active.length} active
              </Badge>
            )}
          </div>
          {active.length === 0 && (
            <p className="mt-0.5 text-[10px] text-muted-foreground">
              No active adapters
            </p>
          )}
        </div>
        <Button
          size="sm"
          variant="outline"
          className="h-7 shrink-0 text-xs"
          onClick={() => setManagerOpen(true)}
        >
          <Settings2 className="mr-1.5 h-3.5 w-3.5" />
          {active.length > 0 ? "Manage" : "Add / Manage"}
        </Button>
      </div>

      {active.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {active.map((selection) => {
            const installed = installedById.get(selection.id);
            const compatibility = classifyLoraCompatibility(
              installed?.base_family,
              ctl.model,
            );
            return (
              <span
                key={selection.id}
                className={`inline-flex max-w-full items-center gap-1 rounded-md border px-2 py-1 text-[11px] ${
                  compatibility === "incompatible"
                    ? "border-amber-500/50 bg-amber-500/5"
                    : "bg-muted/40"
                }`}
              >
                {compatibility === "incompatible" && (
                  <AlertCircle className="h-3 w-3 shrink-0 text-amber-500" />
                )}
                <span
                  className="max-w-48 truncate"
                  title={installed?.name || selection.id}
                >
                  {installed?.name || selection.id}
                </span>
                <span className="tabular-nums text-muted-foreground">
                  {selection.scale.toFixed(2)}
                </span>
                <button
                  type="button"
                  className="ml-0.5 rounded-sm text-muted-foreground hover:text-foreground"
                  onClick={() =>
                    actions.setImageForm({
                      loras: updateLoraSelection(ctl.form.loras, selection.id, {
                        enabled: false,
                      }),
                    })
                  }
                  aria-label={`Disable ${installed?.name || selection.id}`}
                  title="Remove from this generation"
                >
                  <X className="h-3 w-3" />
                </button>
              </span>
            );
          })}
        </div>
      )}

      {disabledForModel.length > 0 && (
        <button
          type="button"
          className="flex items-center gap-1.5 text-left text-[10px] text-amber-600 hover:underline dark:text-amber-400"
          onClick={() => setManagerOpen(true)}
        >
          <AlertCircle className="h-3 w-3 shrink-0" />
          {disabledForModel.length} incompatible selection
          {disabledForModel.length === 1 ? " was" : "s were"} kept but disabled
          for this model
        </button>
      )}
      {missingActive.length > 0 && (
        <ErrorNote
          message={`Active style${missingActive.length === 1 ? "" : "s"} missing from disk: ${missingActive.map((selection) => selection.id).join(", ")}. Remove or reinstall before generating.`}
        />
      )}
      {state.loraError && (
        <ErrorNote message={`LoRA styles unavailable — ${state.loraError}`} />
      )}
      <LoraManagerDialog
        open={managerOpen}
        onOpenChange={setManagerOpen}
        ctl={ctl}
      />
    </div>
  );
}
