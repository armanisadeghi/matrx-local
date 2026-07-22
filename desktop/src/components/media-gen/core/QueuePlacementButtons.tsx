/**
 * QueuePlacementButtons — canonical 3-icon queue control (top / bottom / custom).
 *
 * Quick paths use the loaded model's defaults with no seed. Custom opens a
 * compact popover for overrides, then queues with the same placement semantics.
 */

import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  ArrowDownToLine,
  ArrowUpToLine,
  Check,
  Loader2,
  SlidersHorizontal,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NumberInput } from "@/components/ui/number-input";
import { Label } from "@/components/ui/label";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import {
  buildCustomQueueInput,
  buildQuickQueueInput,
  customQueueSettingsFromDefaults,
  enqueueQuickQueueJob,
  quickQueueBlockedReason,
  type CustomQueueSettings,
  type QueuePlacement,
} from "@/lib/media-gen/quick-queue";

export interface QueuePlacementButtonsProps {
  prompt: string;
  negativePrompt?: string;
  disabled?: boolean;
  /** Prefix so row + editor buttons do not share feedback keys. */
  feedbackKeyPrefix?: string;
  className?: string;
}

export function QueuePlacementButtons({
  prompt,
  negativePrompt = "",
  disabled = false,
  feedbackKeyPrefix = "queue",
  className,
}: QueuePlacementButtonsProps) {
  const [state, actions] = useMediaGenApp();
  const { imageForm, mediaRuntime } = state;
  const { enqueueImageJob } = actions;
  const defaults = imageForm.defaults;
  const imageAvailable =
    mediaRuntime?.state === "ready" && mediaRuntime.image_available === true;

  const [feedbackKey, setFeedbackKey] = useState<string | null>(null);
  const [queueing, setQueueing] = useState<QueuePlacement | "custom" | null>(
    null,
  );
  const [customOpen, setCustomOpen] = useState(false);
  const [customSettings, setCustomSettings] = useState<CustomQueueSettings>(
    () =>
      defaults
        ? customQueueSettingsFromDefaults(defaults)
        : {
            steps: 28,
            guidance: 3.5,
            width: 1024,
            height: 1024,
            seedText: "",
            negativePrompt: "",
          },
  );

  useEffect(() => {
    if (!customOpen || !defaults) return;
    setCustomSettings(customQueueSettingsFromDefaults(defaults));
  }, [customOpen, defaults]);

  const blockedReason = quickQueueBlockedReason(
    defaults,
    imageAvailable,
    prompt,
  );
  const quickDisabled = disabled || blockedReason !== null || queueing !== null;
  const customTriggerDisabled = disabled || queueing !== null;
  const customQueueDisabled =
    disabled || blockedReason !== null || queueing !== null;

  const flash = useCallback((key: string) => {
    setFeedbackKey(key);
    window.setTimeout(
      () => setFeedbackKey((current) => (current === key ? null : current)),
      1200,
    );
  }, []);

  const runQuick = useCallback(
    async (placement: QueuePlacement) => {
      if (!defaults || blockedReason !== null) return;
      const input = buildQuickQueueInput(defaults, { prompt, negativePrompt });
      if (!input) return;
      setQueueing(placement);
      try {
        const ok = await enqueueQuickQueueJob(
          enqueueImageJob,
          input,
          placement,
        );
        if (ok) flash(`${feedbackKeyPrefix}:${placement}`);
      } finally {
        setQueueing(null);
      }
    },
    [
      defaults,
      blockedReason,
      prompt,
      negativePrompt,
      enqueueImageJob,
      flash,
      feedbackKeyPrefix,
    ],
  );

  const runCustom = useCallback(
    async (placement: QueuePlacement) => {
      if (!defaults || blockedReason !== null) return;
      const input = buildCustomQueueInput(
        defaults,
        { prompt, negativePrompt },
        customSettings,
      );
      if (!input) return;
      setQueueing("custom");
      try {
        const ok = await enqueueQuickQueueJob(
          enqueueImageJob,
          input,
          placement,
        );
        if (ok) {
          flash(`${feedbackKeyPrefix}:custom-${placement}`);
          setCustomOpen(false);
        }
      } finally {
        setQueueing(null);
      }
    },
    [
      defaults,
      blockedReason,
      prompt,
      negativePrompt,
      customSettings,
      enqueueImageJob,
      flash,
      feedbackKeyPrefix,
    ],
  );

  const topActive = feedbackKey === `${feedbackKeyPrefix}:top`;
  const bottomActive = feedbackKey === `${feedbackKeyPrefix}:bottom`;
  const customActive =
    feedbackKey === `${feedbackKeyPrefix}:custom-top` ||
    feedbackKey === `${feedbackKeyPrefix}:custom-bottom`;

  return (
    <div
      className={`inline-flex items-center rounded-md border p-0.5 ${className ?? ""}`}
    >
      <QueueIconButton
        label="Queue next"
        title={
          blockedReason ??
          "Queue with model defaults (no seed) — runs after the current job"
        }
        disabled={quickDisabled}
        active={topActive}
        loading={queueing === "top"}
        onClick={() => void runQuick("top")}
      >
        <ArrowUpToLine className="h-3.5 w-3.5" />
      </QueueIconButton>

      <QueueIconButton
        label="Queue last"
        title={
          blockedReason ??
          "Queue with model defaults (no seed) — appended to the end"
        }
        disabled={quickDisabled}
        active={bottomActive}
        loading={queueing === "bottom"}
        onClick={() => void runQuick("bottom")}
      >
        <ArrowDownToLine className="h-3.5 w-3.5" />
      </QueueIconButton>

      <Popover open={customOpen} onOpenChange={setCustomOpen}>
        <Tooltip>
          <TooltipTrigger asChild>
            <PopoverTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="h-6 w-6"
                disabled={customTriggerDisabled}
                aria-label="Custom queue"
                title={blockedReason ?? "Set options, then queue"}
              >
                {queueing === "custom" ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : customActive ? (
                  <Check className="h-3.5 w-3.5 text-green-600 dark:text-green-400" />
                ) : (
                  <SlidersHorizontal className="h-3.5 w-3.5" />
                )}
              </Button>
            </PopoverTrigger>
          </TooltipTrigger>
          <TooltipContent>
            {customActive
              ? "Queued"
              : (blockedReason ?? "Set options, then queue")}
          </TooltipContent>
        </Tooltip>
        <PopoverContent
          className="w-72 space-y-3 p-3"
          align="end"
          onCloseAutoFocus={(event) => event.preventDefault()}
        >
          <p className="text-xs font-medium">Custom queue</p>
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <Label
                htmlFor={`${feedbackKeyPrefix}-steps`}
                className="text-[10px]"
              >
                Steps
              </Label>
              <NumberInput
                id={`${feedbackKeyPrefix}-steps`}
                min={1}
                integer
                value={customSettings.steps}
                onChange={(steps) =>
                  setCustomSettings((prev) => ({ ...prev, steps }))
                }
                emptyValue={customSettings.steps}
                className="h-8 text-xs"
              />
            </div>
            <div className="space-y-1">
              <Label
                htmlFor={`${feedbackKeyPrefix}-guidance`}
                className="text-[10px]"
              >
                Guidance
              </Label>
              <NumberInput
                id={`${feedbackKeyPrefix}-guidance`}
                step={0.1}
                integer={false}
                value={customSettings.guidance}
                onChange={(guidance) =>
                  setCustomSettings((prev) => ({ ...prev, guidance }))
                }
                emptyValue={customSettings.guidance}
                className="h-8 text-xs"
              />
            </div>
            <div className="space-y-1">
              <Label
                htmlFor={`${feedbackKeyPrefix}-width`}
                className="text-[10px]"
              >
                Width
              </Label>
              <NumberInput
                id={`${feedbackKeyPrefix}-width`}
                min={64}
                integer
                value={customSettings.width}
                onChange={(width) =>
                  setCustomSettings((prev) => ({ ...prev, width }))
                }
                emptyValue={customSettings.width}
                className="h-8 text-xs"
              />
            </div>
            <div className="space-y-1">
              <Label
                htmlFor={`${feedbackKeyPrefix}-height`}
                className="text-[10px]"
              >
                Height
              </Label>
              <NumberInput
                id={`${feedbackKeyPrefix}-height`}
                min={64}
                integer
                value={customSettings.height}
                onChange={(height) =>
                  setCustomSettings((prev) => ({ ...prev, height }))
                }
                emptyValue={customSettings.height}
                className="h-8 text-xs"
              />
            </div>
          </div>
          <div className="space-y-1">
            <Label
              htmlFor={`${feedbackKeyPrefix}-seed`}
              className="text-[10px]"
            >
              Seed (optional)
            </Label>
            <Input
              id={`${feedbackKeyPrefix}-seed`}
              value={customSettings.seedText}
              onChange={(e) =>
                setCustomSettings((prev) => ({
                  ...prev,
                  seedText: e.target.value,
                }))
              }
              placeholder="Leave empty for random"
              className="h-8 text-xs"
            />
          </div>
          {defaults?.supportsNegativePrompt && (
            <div className="space-y-1">
              <Label
                htmlFor={`${feedbackKeyPrefix}-negative`}
                className="text-[10px]"
              >
                Negative
              </Label>
              <Input
                id={`${feedbackKeyPrefix}-negative`}
                value={customSettings.negativePrompt}
                onChange={(e) =>
                  setCustomSettings((prev) => ({
                    ...prev,
                    negativePrompt: e.target.value,
                  }))
                }
                className="h-8 text-xs"
              />
            </div>
          )}
          <div className="flex gap-2 pt-1">
            <Button
              type="button"
              size="sm"
              className="h-8 flex-1 gap-1 text-xs"
              disabled={customQueueDisabled}
              onClick={() => void runCustom("top")}
            >
              <ArrowUpToLine className="h-3.5 w-3.5" />
              Queue next
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-8 flex-1 gap-1 text-xs"
              disabled={customQueueDisabled}
              onClick={() => void runCustom("bottom")}
            >
              <ArrowDownToLine className="h-3.5 w-3.5" />
              Queue last
            </Button>
          </div>
        </PopoverContent>
      </Popover>
    </div>
  );
}

function QueueIconButton({
  label,
  title,
  disabled,
  active,
  loading,
  onClick,
  children,
}: {
  label: string;
  title: string;
  disabled: boolean;
  active: boolean;
  loading: boolean;
  onClick?: () => void;
  children: ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-6 w-6"
          disabled={disabled}
          aria-label={label}
          title={title}
          onClick={onClick}
        >
          {loading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : active ? (
            <Check className="h-3.5 w-3.5 text-green-600 dark:text-green-400" />
          ) : (
            children
          )}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{active ? "Queued" : title}</TooltipContent>
    </Tooltip>
  );
}
