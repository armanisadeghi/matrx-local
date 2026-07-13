/**
 * MediaContextMenu — the right-click menu for ANY image/video in the app.
 *
 * Rendered once, by MediaActionsProvider, into a portal. Surfaces do not mount
 * it; they call `actions.openContextMenu(descriptor, {x, y})` (or simply use
 * <MediaThumb>, which wires onContextMenu for them).
 *
 * Delete asks for confirmation in place — a right-click stray must never
 * destroy an image.
 */

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useMediaActions } from "./MediaActionsProvider";
import { buildMediaMenu } from "./MediaMenuItems";
import { mediaTitle, type MediaDescriptor } from "./types";

const MENU_WIDTH = 232;
const EDGE_PAD = 8;

export function MediaContextMenu({
  item,
  position,
  onClose,
}: {
  item: MediaDescriptor;
  position: { x: number; y: number };
  onClose: () => void;
}) {
  const actions = useMediaActions();
  const ref = useRef<HTMLDivElement | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [pos, setPos] = useState(position);

  // Keep the menu on screen (a right-click near the bottom-right edge must not
  // open a menu the user cannot see or reach).
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const { width, height } = el.getBoundingClientRect();
    setPos({
      x: Math.min(position.x, window.innerWidth - width - EDGE_PAD),
      y: Math.min(position.y, window.innerHeight - height - EDGE_PAD),
    });
  }, [position]);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("contextmenu", onDown);
    window.addEventListener("keydown", onKey);
    window.addEventListener("resize", onClose);
    window.addEventListener("scroll", onClose, true);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("contextmenu", onDown);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onClose);
      window.removeEventListener("scroll", onClose, true);
    };
  }, [onClose]);

  const entries = buildMediaMenu(item, actions, { onAfter: onClose });
  const title = mediaTitle(item);

  return createPortal(
    <div
      ref={ref}
      role="menu"
      aria-label="Media actions"
      style={{ left: pos.x, top: pos.y, width: MENU_WIDTH }}
      className="fixed z-[10000] overflow-hidden rounded-lg border bg-popover py-1 text-popover-foreground shadow-xl animate-in fade-in zoom-in-95 duration-100"
    >
      <p
        className="truncate px-3 py-1.5 text-[10px] text-muted-foreground"
        title={title}
      >
        {title || "(no prompt)"}
      </p>
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
  );
}
