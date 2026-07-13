/**
 * MediaOverflowMenu — the canonical "⋯" button.
 *
 * Same entries as the right-click menu (both come from buildMediaMenu), so
 * whichever affordance the user reaches for, the abilities are identical.
 * Drop it on any card, tile, row or bar that shows a piece of media.
 */

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { MoreHorizontal } from "lucide-react";
import { useMediaActions } from "./MediaActionsProvider";
import { buildMediaMenu } from "./MediaMenuItems";
import type { MediaDescriptor } from "./types";

const MENU_WIDTH = 232;
const EDGE_PAD = 8;

/**
 * - "surface" — normal light/dark app chrome (cards, panels)
 * - "overlay" — a pill floating ON an image (hover chrome on a thumbnail)
 * - "bar"     — inside the lightbox's dark top bar (matches its BarButtons)
 */
export type MediaMenuTone = "surface" | "overlay" | "bar";

const TONE: Record<MediaMenuTone, string> = {
  surface: "text-muted-foreground hover:bg-muted hover:text-foreground",
  overlay: "bg-black/50 text-white/85 hover:bg-black/70 hover:text-white",
  bar: "text-zinc-300 hover:bg-white/10 hover:text-white",
};

export function MediaOverflowMenu({
  item,
  omit,
  className = "",
  tone = "surface",
  label = "More actions",
}: {
  item: MediaDescriptor;
  omit?: string[];
  className?: string;
  tone?: MediaMenuTone;
  label?: string;
}) {
  const actions = useMediaActions();
  const btnRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  useEffect(() => {
    if (!open) {
      setConfirmingDelete(false);
      return;
    }
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (menuRef.current?.contains(t) || btnRef.current?.contains(t)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    const close = () => setOpen(false);
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [open]);

  const toggle = (e: React.MouseEvent) => {
    // Never let the click reach a parent card that would open the lightbox.
    e.preventDefault();
    e.stopPropagation();
    const rect = btnRef.current?.getBoundingClientRect();
    if (rect) {
      setPos({
        x: Math.min(rect.right - MENU_WIDTH, window.innerWidth - MENU_WIDTH - EDGE_PAD),
        y: rect.bottom + 4,
      });
    }
    setOpen((o) => !o);
  };

  const entries = buildMediaMenu(item, actions, {
    ...(omit ? { omit } : {}),
    onAfter: () => setOpen(false),
  });

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        onClick={toggle}
        aria-label={label}
        title={label}
        aria-haspopup="menu"
        aria-expanded={open}
        className={`flex h-7 w-7 items-center justify-center rounded-md transition-colors ${TONE[tone]} ${className}`}
      >
        <MoreHorizontal className="h-4 w-4" />
      </button>
      {open &&
        pos &&
        createPortal(
          <div
            ref={menuRef}
            role="menu"
            aria-label="Media actions"
            style={{ left: Math.max(EDGE_PAD, pos.x), top: pos.y, width: MENU_WIDTH }}
            className="fixed z-[10000] overflow-hidden rounded-lg border bg-popover py-1 text-popover-foreground shadow-xl animate-in fade-in zoom-in-95 duration-100"
            onClick={(e) => e.stopPropagation()}
          >
            {entries.map((e) => {
              const isDelete = e.id === "delete";
              const armed = isDelete && confirmingDelete;
              return (
                <div key={e.id}>
                  {e.separatorBefore && <div className="my-1 h-px bg-border" />}
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      if (isDelete && !armed) {
                        setConfirmingDelete(true);
                        return;
                      }
                      e.run();
                    }}
                    className={`flex w-full items-center gap-2.5 px-3 py-1.5 text-left text-xs transition-colors ${
                      e.danger
                        ? "text-destructive hover:bg-destructive/10"
                        : "hover:bg-muted"
                    } ${armed ? "bg-destructive/10 font-medium" : ""}`}
                  >
                    {e.icon}
                    <span className="min-w-0 flex-1 truncate">
                      {armed ? "Click again to delete" : e.label}
                    </span>
                  </button>
                </div>
              );
            })}
          </div>,
          document.body,
        )}
    </>
  );
}
