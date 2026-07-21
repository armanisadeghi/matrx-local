/**
 * MediaGenSurface — one wrapper for page / dialog / popover hosts.
 * Canonical section cores render inside this; tabs and image-gen buttons
 * only choose the host kind.
 */

import type { ReactNode } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";

export type MediaGenSurfaceKind = "page" | "dialog" | "popover";

export interface MediaGenSurfaceProps {
  kind: MediaGenSurfaceKind;
  title?: string;
  description?: string;
  /** dialog + popover */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** popover only */
  trigger?: ReactNode;
  /**
   * When true (default), popover hosts render as a viewport-centered dialog
   * instead of anchoring to the trigger — large pickers stay fully on screen.
   */
  centered?: boolean;
  children: ReactNode;
  className?: string;
  contentClassName?: string;
}

const POPOVER_PANEL =
  "flex h-[min(560px,72vh)] w-[min(920px,92vw)] flex-col overflow-hidden p-0";

const CENTERED_PANEL =
  "flex h-[min(560px,80vh)] w-full max-w-[min(920px,92vw)] flex-col overflow-hidden gap-0 p-0";

const DIALOG_PANEL =
  "flex h-[85vh] max-w-5xl flex-col overflow-hidden gap-0 p-0";

export function MediaGenSurface({
  kind,
  title,
  description,
  open,
  onOpenChange,
  trigger,
  centered = true,
  children,
  className,
  contentClassName,
}: MediaGenSurfaceProps) {
  if (kind === "page") {
    return (
      <div className={`flex h-full min-h-0 flex-col ${className ?? ""}`}>
        {children}
      </div>
    );
  }

  const body = (
    <>
      {title && (
        <div className="shrink-0 border-b px-4 py-3">
          <h2 className="text-sm font-semibold leading-none">{title}</h2>
          {description && (
            <p className="mt-1.5 text-xs text-muted-foreground">
              {description}
            </p>
          )}
        </div>
      )}
      <div
        className={`min-h-0 flex-1 overflow-hidden ${contentClassName ?? "p-4"}`}
      >
        {children}
      </div>
    </>
  );

  if (kind === "dialog") {
    return (
      <Dialog
        {...(open !== undefined ? { open } : {})}
        {...(onOpenChange !== undefined ? { onOpenChange } : {})}
      >
        <DialogContent className={`${DIALOG_PANEL} ${className ?? ""}`}>
          {title && (
            <DialogHeader className="shrink-0 border-b px-4 py-3 text-left">
              <DialogTitle>{title}</DialogTitle>
              {description && (
                <DialogDescription>{description}</DialogDescription>
              )}
            </DialogHeader>
          )}
          <div
            className={`min-h-0 flex-1 overflow-hidden ${contentClassName ?? "p-4"}`}
          >
            {children}
          </div>
        </DialogContent>
      </Dialog>
    );
  }

  if (kind === "popover" && centered) {
    return (
      <Dialog
        {...(open !== undefined ? { open } : {})}
        {...(onOpenChange !== undefined ? { onOpenChange } : {})}
      >
        {trigger && <DialogTrigger asChild>{trigger}</DialogTrigger>}
        <DialogContent className={`${CENTERED_PANEL} ${className ?? ""}`}>
          {title && (
            <DialogHeader className="shrink-0 border-b px-4 py-3 text-left">
              <DialogTitle className="text-sm font-semibold">
                {title}
              </DialogTitle>
              {description && (
                <DialogDescription className="text-xs">
                  {description}
                </DialogDescription>
              )}
            </DialogHeader>
          )}
          <div
            className={`min-h-0 flex-1 overflow-hidden ${contentClassName ?? "p-4"}`}
          >
            {children}
          </div>
        </DialogContent>
      </Dialog>
    );
  }

  return (
    <Popover
      {...(open !== undefined ? { open } : {})}
      {...(onOpenChange !== undefined ? { onOpenChange } : {})}
    >
      {trigger && <PopoverTrigger asChild>{trigger}</PopoverTrigger>}
      <PopoverContent
        align="start"
        className={`${POPOVER_PANEL} ${className ?? ""}`}
      >
        {body}
      </PopoverContent>
    </Popover>
  );
}
