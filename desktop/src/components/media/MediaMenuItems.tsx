/**
 * The ONE menu definition for media.
 *
 * Both the "⋯" overflow menu and the right-click context menu render this same
 * list, so an image offers exactly the same abilities however you reached it.
 * An action is only listed when it can actually run (see capabilitiesOf) — a
 * menu never shows a dead entry.
 */

import type { JSX } from "react";
import {
  Copy,
  Download,
  FolderOpen,
  ImageIcon,
  ImagePlus,
  Info,
  Lock,
  Maximize2,
  Repeat2,
  Sparkles,
  Sprout,
  Trash2,
  Undo2,
} from "lucide-react";
import type { MediaActions } from "./MediaActionsProvider";
import { capabilitiesOf, type MediaDescriptor } from "./types";

export interface MediaMenuEntry {
  id: string;
  label: string;
  icon: JSX.Element;
  run: () => void;
  /** Renders red and (in menus that support it) asks for confirmation. */
  danger?: boolean;
  /** Starts a new visual group above this entry. */
  separatorBefore?: boolean;
}

const ICON = "h-3.5 w-3.5 shrink-0";

/**
 * Build the canonical menu for a descriptor.
 *
 * `omit` drops entries that would be redundant in the current surface (e.g.
 * the lightbox does not need "Open full size" — you are already there).
 */
export function buildMediaMenu(
  item: MediaDescriptor,
  actions: MediaActions,
  opts: { omit?: string[]; onAfter?: () => void } = {},
): MediaMenuEntry[] {
  const caps = capabilitiesOf(item);
  const after = opts.onAfter;
  const wrap = (fn: () => unknown) => () => {
    void Promise.resolve(fn()).finally(() => after?.());
  };

  const entries: MediaMenuEntry[] = [
    {
      id: "open",
      label: "Open full size",
      icon: <Maximize2 className={ICON} />,
      run: wrap(() => actions.openOne(item)),
    },
    {
      id: "info",
      label: "Info & metadata",
      icon: <Info className={ICON} />,
      run: wrap(() => actions.info(item)),
    },
    {
      id: "remix",
      label: "Remix",
      icon: <Repeat2 className={ICON} />,
      separatorBefore: true,
      run: wrap(() => actions.remix(item)),
    },
    {
      id: "iterate",
      label: "Iterate from this image",
      icon: <Sparkles className={ICON} />,
      run: wrap(() => actions.iterate(item)),
    },
    {
      id: "use-as-input",
      label: "Use as input image",
      icon: <ImagePlus className={ICON} />,
      run: wrap(() => actions.useAsInput(item)),
    },
    {
      id: "reuse-seed",
      label:
        typeof item.seed === "number" ? `Reuse seed ${item.seed}` : "Reuse seed",
      icon: <Sprout className={ICON} />,
      run: wrap(() => actions.reuseSeed(item)),
    },
    {
      id: "copy-image",
      label: "Copy image",
      icon: <ImageIcon className={ICON} />,
      separatorBefore: true,
      run: wrap(() => actions.copyImage(item)),
    },
    {
      id: "copy-prompt",
      label: "Copy prompt",
      icon: <Copy className={ICON} />,
      run: wrap(() => actions.copyPrompt(item)),
    },
    {
      id: "download",
      label: "Download",
      icon: <Download className={ICON} />,
      run: wrap(() => actions.download(item)),
    },
    {
      id: "show-in-folder",
      label: "Show in folder",
      icon: <FolderOpen className={ICON} />,
      run: wrap(() => actions.showInFolder(item)),
    },
    {
      id: "vault",
      label: "Move to Private",
      icon: <Lock className={ICON} />,
      separatorBefore: true,
      run: wrap(() => actions.moveToVault(item)),
    },
    {
      id: "restore",
      label: "Restore from Private",
      icon: <Undo2 className={ICON} />,
      separatorBefore: true,
      run: wrap(() => actions.restoreFromVault(item)),
    },
    {
      id: "delete",
      label: "Delete",
      icon: <Trash2 className={ICON} />,
      danger: true,
      separatorBefore: true,
      run: wrap(() => actions.remove(item)),
    },
  ];

  const allowed: Record<string, boolean> = {
    open: true,
    info: true,
    remix: caps.canRemix,
    iterate: caps.canIterate,
    "use-as-input": caps.canUseAsInput,
    "reuse-seed": caps.canReuseSeed,
    "copy-image": caps.canCopyImage,
    "copy-prompt": caps.canCopyPrompt,
    download: caps.canDownload,
    "show-in-folder": caps.canShowInFolder,
    vault: caps.canVault,
    restore: caps.canRestore,
    delete: caps.canDelete,
  };

  const omit = new Set(opts.omit ?? []);
  return entries.filter((e) => allowed[e.id] && !omit.has(e.id));
}
