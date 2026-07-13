/**
 * MediaLightbox — full-viewport media viewer for generated images & videos.
 *
 * "With media, give the user unlimited power": click-to-expand, true
 * fullscreen, wheel/pinch zoom toward the cursor, drag pan, keyboard-first
 * navigation, download, seed reuse, and a metadata info panel.
 *
 * Pure presentation: ALL state is local. Rendered via a portal to
 * document.body so it sits above every layout container. Deliberately styled
 * dark (conventional for lightboxes) so it reads identically in both themes.
 *
 * Zoom model: the media element gets `translate(tx,ty) scale(s)` with a
 * center origin — transform-only, so zoom/pan never trigger layout.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { JSX } from "react";
import { createPortal } from "react-dom";
import {
  ChevronLeft,
  ChevronRight,
  Download,
  ImagePlus,
  Info,
  Lock,
  Maximize,
  Maximize2,
  Minimize,
  Minus,
  Plus,
  Repeat2,
  RotateCcw,
  Sprout,
  X,
} from "lucide-react";
import { useMediaActions } from "@/components/media/MediaActionsProvider";
import { MediaOverflowMenu } from "@/components/media/MediaOverflowMenu";
import { CopyButton } from "@/components/media/MediaInfoDialog";
import {
  capabilitiesOf,
  extraParams,
  mediaTitle,
  type MediaDescriptor,
} from "@/components/media/types";

// ── Internals ────────────────────────────────────────────────────────────────

const MIN_SCALE = 0.25;
const MAX_SCALE = 12;
const BAR_HIDE_MS = 2000;

function clampScale(s: number): number {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, s));
}

/** requestFullscreen with WebKit fallback; rejections logged, never thrown. */
function enterFullscreen(el: HTMLElement): void {
  type FsElement = HTMLElement & {
    webkitRequestFullscreen?: () => Promise<void> | void;
  };
  const fsEl = el as FsElement;
  if (typeof el.requestFullscreen === "function") {
    el.requestFullscreen().catch((err: unknown) => {
      console.error("[media-lightbox] requestFullscreen rejected:", err);
    });
  } else if (typeof fsEl.webkitRequestFullscreen === "function") {
    try {
      void fsEl.webkitRequestFullscreen();
    } catch (err) {
      console.error("[media-lightbox] webkitRequestFullscreen failed:", err);
    }
  } else {
    console.error("[media-lightbox] Fullscreen API not available");
  }
}

function exitFullscreen(): void {
  type FsDocument = Document & {
    webkitExitFullscreen?: () => Promise<void> | void;
  };
  const doc = document as FsDocument;
  if (typeof document.exitFullscreen === "function") {
    document.exitFullscreen().catch((err: unknown) => {
      console.error("[media-lightbox] exitFullscreen rejected:", err);
    });
  } else if (typeof doc.webkitExitFullscreen === "function") {
    try {
      void doc.webkitExitFullscreen();
    } catch (err) {
      console.error("[media-lightbox] webkitExitFullscreen failed:", err);
    }
  }
}

function fullscreenElement(): Element | null {
  type FsDocument = Document & { webkitFullscreenElement?: Element | null };
  return (
    document.fullscreenElement ??
    (document as FsDocument).webkitFullscreenElement ??
    null
  );
}

/** Icon button used across the chrome (top bar, chevrons, zoom controls). */
function BarButton({
  onClick,
  label,
  title,
  active = false,
  danger = false,
  children,
}: {
  onClick: () => void;
  label: string;
  title?: string;
  active?: boolean;
  danger?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={title ?? label}
      className={`flex h-8 w-8 items-center justify-center rounded-md transition-colors ${
        danger
          ? "text-red-400 hover:bg-red-500/20 hover:text-red-300"
          : active
            ? "bg-white/20 text-white"
            : "text-zinc-300 hover:bg-white/10 hover:text-white"
      }`}
    >
      {children}
    </button>
  );
}

// ── Component ────────────────────────────────────────────────────────────────

/**
 * The full-viewport viewer. Rendered ONCE by MediaActionsProvider — surfaces
 * never mount it; they call `actions.open(viewingSet, index)`.
 *
 * Every action lives here (via the shared "⋯" menu and the right-click menu),
 * so a user who reached full-size from a 24px icon has the same abilities as
 * one who came from the library grid.
 */
export function MediaLightbox({
  open,
  items,
  startIndex = 0,
  onClose,
}: {
  open: boolean;
  items: MediaDescriptor[];
  startIndex?: number;
  onClose: () => void;
}): JSX.Element | null {
  const mediaActions = useMediaActions();
  const [index, setIndex] = useState(0);
  const [scale, setScale] = useState(1);
  const [tx, setTx] = useState(0);
  const [ty, setTy] = useState(0);
  const [showInfo, setShowInfo] = useState(false);
  const [barVisible, setBarVisible] = useState(true);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [dragging, setDragging] = useState(false);
  /**
   * True while the pointer is over ANY chrome (top bar, chevrons, zoom bar).
   *
   * THE "it exits after ~3 images" BUG: the chrome auto-hides 2s after the last
   * mouse move, and hidden chrome is `pointer-events-none`. Clicking Next
   * repeatedly without moving the mouse let the timer fire between clicks — the
   * next click passed straight THROUGH the invisible chevron to the stage
   * backdrop, whose click handler closes the lightbox. Chrome under the pointer
   * now stays interactive no matter what the idle timer does, so a click on a
   * chevron always lands on the chevron.
   */
  const [hoveringChrome, setHoveringChrome] = useState(false);

  const overlayRef = useRef<HTMLDivElement | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const barTimerRef = useRef<number | null>(null);
  const dragRef = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    startTx: number;
    startTy: number;
  } | null>(null);
  // Live transform values for the native wheel listener (avoids re-binding).
  const viewRef = useRef({ scale: 1, tx: 0, ty: 0 });
  viewRef.current = { scale, tx, ty };

  const count = items.length;
  const safeIndex = count > 0 ? Math.min(Math.max(index, 0), count - 1) : 0;
  const current = count > 0 ? items[safeIndex] : null;

  const resetView = useCallback(() => {
    setScale(1);
    setTx(0);
    setTy(0);
  }, []);

  // Re-seed on open / startIndex change.
  useEffect(() => {
    if (!open) return;
    setIndex(Math.min(Math.max(startIndex, 0), Math.max(items.length - 1, 0)));
    setScale(1);
    setTx(0);
    setTy(0);
    setBarVisible(true);
    // items.length intentionally read once at open — re-anchoring on a live
    // set change is handled by the effect below, which is id-based.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, startIndex]);

  /**
   * Re-anchor on the CURRENT item id whenever the viewing set changes.
   *
   * The library's viewing set only contains items whose bytes have resolved, so
   * it GROWS while the user is already paging (and SHRINKS on a delete/vault).
   * An index into a shifting array silently points at a different image — the
   * other half of the "it jumps out / skips around after a few images" report.
   * Tracking the id makes the position mean what the user sees.
   */
  const currentIdRef = useRef<string | null>(null);
  useEffect(() => {
    if (open && current) currentIdRef.current = current.id;
  }, [open, current]);
  const itemIdsKey = items.map((i) => i.id).join("\u0000");
  useEffect(() => {
    if (!open) return;
    const id = currentIdRef.current;
    if (!id) return;
    const at = items.findIndex((i) => i.id === id);
    // Not found = the item left the set (deleted/vaulted). The provider already
    // pruned it and clamps the index, so leave the slot alone.
    if (at >= 0) setIndex((prev) => (prev === at ? prev : at));
    // itemIdsKey is the value that actually matters; `items` identity churns.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, itemIdsKey]);

  const goTo = useCallback(
    (dir: 1 | -1) => {
      if (count < 2) return;
      setIndex((i) => (i + dir + count) % count);
      setScale(1);
      setTx(0);
      setTy(0);
    },
    [count],
  );

  /** Zoom toward a viewport point, keeping that point visually fixed. */
  const zoomToward = useCallback(
    (nextScaleRaw: number, clientX?: number, clientY?: number) => {
      const nextScale = clampScale(nextScaleRaw);
      const stage = stageRef.current;
      const { scale: s, tx: cx, ty: cy } = viewRef.current;
      if (!stage || s === nextScale) {
        setScale(nextScale);
        return;
      }
      const rect = stage.getBoundingClientRect();
      const px = (clientX ?? rect.left + rect.width / 2) - rect.left - rect.width / 2;
      const py = (clientY ?? rect.top + rect.height / 2) - rect.top - rect.height / 2;
      setScale(nextScale);
      setTx(px - ((px - cx) * nextScale) / s);
      setTy(py - ((py - cy) * nextScale) / s);
    },
    [],
  );

  /** Scale at which the image renders at its natural 1:1 pixel size. */
  const naturalScale = useCallback((): number => {
    const img = imgRef.current;
    if (!img || img.clientWidth === 0 || img.naturalWidth === 0) return 2;
    return img.naturalWidth / img.clientWidth;
  }, []);

  const handleDoubleClick = useCallback(
    (e: React.MouseEvent) => {
      const s100 = naturalScale();
      const { scale: s } = viewRef.current;
      if (Math.abs(s - 1) < 0.05) {
        // fit → 100% (or 2x when the image is already shown ~natural size)
        zoomToward(s100 > 1.1 ? s100 : 2, e.clientX, e.clientY);
      } else if (s < Math.max(s100, 2) - 0.05) {
        zoomToward(Math.max(s100 * 2, 2), e.clientX, e.clientY);
      } else {
        resetView();
      }
    },
    [naturalScale, zoomToward, resetView],
  );

  // Native non-passive wheel listener (React's onWheel is passive at the
  // root, so preventDefault would be ignored). Trackpad pinch arrives as
  // wheel+ctrlKey and flows through the same path.
  useEffect(() => {
    if (!open) return;
    const stage = stageRef.current;
    if (!stage) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      if (current?.kind !== "image") return;
      const factor = Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0022));
      zoomToward(viewRef.current.scale * factor, e.clientX, e.clientY);
    };
    stage.addEventListener("wheel", onWheel, { passive: false });
    return () => stage.removeEventListener("wheel", onWheel);
  }, [open, current?.kind, zoomToward]);

  // Drag to pan (any zoom level other than fit; at fit the image is static
  // and a click on the backdrop closes).
  const handlePointerDown = useCallback((e: React.PointerEvent) => {
    if (viewRef.current.scale === 1) return;
    if (e.button !== 0) return;
    e.preventDefault();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    dragRef.current = {
      pointerId: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      startTx: viewRef.current.tx,
      startTy: viewRef.current.ty,
    };
    setDragging(true);
  }, []);

  const handlePointerMove = useCallback((e: React.PointerEvent) => {
    const d = dragRef.current;
    if (!d || d.pointerId !== e.pointerId) return;
    setTx(d.startTx + (e.clientX - d.startX));
    setTy(d.startTy + (e.clientY - d.startY));
  }, []);

  const handlePointerUp = useCallback((e: React.PointerEvent) => {
    if (dragRef.current?.pointerId === e.pointerId) {
      dragRef.current = null;
      setDragging(false);
    }
  }, []);

  // Fullscreen state tracking — the icon must stay honest even when the user
  // exits via Esc or the OS UI.
  useEffect(() => {
    if (!open) return;
    const onChange = () => setIsFullscreen(fullscreenElement() != null);
    document.addEventListener("fullscreenchange", onChange);
    document.addEventListener("webkitfullscreenchange", onChange);
    return () => {
      document.removeEventListener("fullscreenchange", onChange);
      document.removeEventListener("webkitfullscreenchange", onChange);
    };
  }, [open]);

  const toggleFullscreen = useCallback(() => {
    if (fullscreenElement()) {
      exitFullscreen();
    } else if (overlayRef.current) {
      enterFullscreen(overlayRef.current);
    }
  }, []);

  // Leaving fullscreen when the lightbox itself closes.
  useEffect(() => {
    if (open) return;
    if (fullscreenElement()) exitFullscreen();
  }, [open]);

  // Body scroll lock while open.
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  // Keyboard shortcuts.
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      switch (e.key) {
        case "Escape":
          e.preventDefault();
          onClose();
          break;
        case "ArrowLeft":
          e.preventDefault();
          goTo(-1);
          break;
        case "ArrowRight":
          e.preventDefault();
          goTo(1);
          break;
        case "+":
        case "=":
          e.preventDefault();
          zoomToward(viewRef.current.scale * 1.3);
          break;
        case "-":
        case "_":
          e.preventDefault();
          zoomToward(viewRef.current.scale / 1.3);
          break;
        case "0":
          e.preventDefault();
          resetView();
          break;
        case "f":
        case "F":
          e.preventDefault();
          toggleFullscreen();
          break;
        case "i":
        case "I":
          e.preventDefault();
          setShowInfo((v) => !v);
          break;
        default:
          break;
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose, goTo, zoomToward, resetView, toggleFullscreen]);

  // Auto-hide chrome after mouse idle.
  const pokeBar = useCallback(() => {
    setBarVisible(true);
    if (barTimerRef.current !== null) window.clearTimeout(barTimerRef.current);
    barTimerRef.current = window.setTimeout(
      () => setBarVisible(false),
      BAR_HIDE_MS,
    );
  }, []);
  useEffect(() => {
    if (!open) return;
    pokeBar();
    return () => {
      if (barTimerRef.current !== null) {
        window.clearTimeout(barTimerRef.current);
        barTimerRef.current = null;
      }
    };
  }, [open, pokeBar]);

  /**
   * Props every chrome container gets. Entering pins the chrome open (and
   * cancels the idle timer); leaving re-arms it. This is what guarantees a
   * click on a chevron can never fall through to the backdrop — see the
   * hoveringChrome comment above.
   */
  const chromeHover = useMemo(
    () => ({
      onMouseEnter: () => {
        if (barTimerRef.current !== null) {
          window.clearTimeout(barTimerRef.current);
          barTimerRef.current = null;
        }
        setBarVisible(true);
        setHoveringChrome(true);
      },
      onMouseLeave: () => {
        setHoveringChrome(false);
        pokeBar();
      },
    }),
    [pokeBar],
  );

  // Neighbor preload (images only).
  const neighbors = useMemo(() => {
    if (count < 2) return [] as string[];
    const urls: string[] = [];
    for (const off of [1, count - 1]) {
      const item = items[(safeIndex + off) % count];
      if (item && item.kind === "image") urls.push(item.url);
    }
    return [...new Set(urls)];
  }, [items, safeIndex, count]);

  const metaJson = useMemo(() => {
    if (!current) return null;
    const rest = extraParams(current);
    return Object.keys(rest).length > 0 ? JSON.stringify(rest, null, 2) : null;
  }, [current]);

  if (!open || !current) return null;

  const caps = capabilitiesOf(current);
  const chromeVisible = barVisible || showInfo || dragging || hoveringChrome;
  const heading = mediaTitle(current);
  const zoomPercent = Math.round(scale * 100);

  const overlay = (
    <div
      ref={overlayRef}
      role="dialog"
      aria-modal="true"
      aria-label={heading || "Media viewer"}
      onMouseMove={pokeBar}
      className="fixed inset-0 z-[9999] flex flex-col bg-black/90 backdrop-blur-sm animate-in fade-in duration-200"
    >
      {/* ── Top bar ─────────────────────────────────────────────────────── */}
      <div
        {...chromeHover}
        className={`absolute inset-x-0 top-0 z-20 flex items-center gap-2 bg-gradient-to-b from-black/80 to-transparent px-3 py-2 transition-opacity duration-300 ${
          chromeVisible ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
      >
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm text-zinc-100" title={heading}>
            {heading || "(no prompt)"}
          </p>
          <p className="text-[11px] tabular-nums text-zinc-400">
            {safeIndex + 1} / {count}
            {current.kind === "image" && scale !== 1 && ` · ${zoomPercent}%`}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          <BarButton
            onClick={() => setShowInfo((v) => !v)}
            label="Toggle info panel"
            title="Info (I)"
            active={showInfo}
          >
            <Info className="h-4 w-4" />
          </BarButton>
          {caps.canRemix && (
            <BarButton
              onClick={() => void mediaActions.remix(current)}
              label="Remix"
              title="Remix — reload everything that made this image"
            >
              <Repeat2 className="h-4 w-4" />
            </BarButton>
          )}
          <BarButton
            onClick={() => void mediaActions.download(current)}
            label="Download"
            title="Download"
          >
            <Download className="h-4 w-4" />
          </BarButton>
          {caps.canReuseSeed && (
            <BarButton
              onClick={() => mediaActions.reuseSeed(current)}
              label="Reuse seed"
              title={`Reuse seed ${current.seed}`}
            >
              <Sprout className="h-4 w-4" />
            </BarButton>
          )}
          {caps.canUseAsInput && (
            <BarButton
              onClick={() => void mediaActions.useAsInput(current)}
              label="Use as input image"
              title="Use as the img2img input image"
            >
              <ImagePlus className="h-4 w-4" />
            </BarButton>
          )}
          {caps.canVault && (
            <BarButton
              onClick={() => void mediaActions.moveToVault(current)}
              label="Move to Private"
              title="Move into the Private vault"
            >
              <Lock className="h-4 w-4" />
            </BarButton>
          )}
          {/* Everything else (copy image, copy prompt, show in folder, delete
              with confirm, restore) — the SAME menu as right-click. */}
          <MediaOverflowMenu
            item={current}
            tone="bar"
            omit={["open", "info"]}
            className="h-8 w-8"
          />
          <BarButton
            onClick={toggleFullscreen}
            label={isFullscreen ? "Exit fullscreen" : "Enter fullscreen"}
            title="Fullscreen (F)"
            active={isFullscreen}
          >
            {isFullscreen ? (
              <Minimize className="h-4 w-4" />
            ) : (
              <Maximize className="h-4 w-4" />
            )}
          </BarButton>
          <BarButton onClick={onClose} label="Close" title="Close (Esc)">
            <X className="h-4 w-4" />
          </BarButton>
        </div>
      </div>

      {/* ── Stage + info panel ──────────────────────────────────────────── */}
      <div className="flex min-h-0 flex-1">
        <div
          ref={stageRef}
          onClick={(e) => {
            // Backdrop click closes — only the stage itself, never the media,
            // and never a click that started on (now-hidden) chrome.
            if (e.target === e.currentTarget && !hoveringChrome) onClose();
          }}
          className="relative flex min-w-0 flex-1 items-center justify-center overflow-hidden"
        >
          {current.kind === "image" ? (
            <img
              ref={imgRef}
              key={current.id}
              src={current.url}
              alt={heading || "Generated media"}
              draggable={false}
              onContextMenu={(e) => {
                e.preventDefault();
                // Without stopPropagation the event reaches the context menu's
                // own window-level dismiss listener, which closes the menu we
                // just opened — a second right-click would appear to do nothing.
                e.stopPropagation();
                mediaActions.openContextMenu(current, {
                  x: e.clientX,
                  y: e.clientY,
                });
              }}
              onDoubleClick={handleDoubleClick}
              onPointerDown={handlePointerDown}
              onPointerMove={handlePointerMove}
              onPointerUp={handlePointerUp}
              onPointerCancel={handlePointerUp}
              style={{
                transform: `translate(${tx}px, ${ty}px) scale(${scale})`,
                transition: dragging ? "none" : "transform 120ms ease-out",
                cursor:
                  scale !== 1 ? (dragging ? "grabbing" : "grab") : "zoom-in",
              }}
              className="max-h-full max-w-full select-none object-contain animate-in zoom-in-95 duration-200 will-change-transform"
            />
          ) : (
            <video
              key={current.id}
              controls
              autoPlay
              loop
              src={current.url}
              onContextMenu={(e) => {
                e.preventDefault();
                // Without stopPropagation the event reaches the context menu's
                // own window-level dismiss listener, which closes the menu we
                // just opened — a second right-click would appear to do nothing.
                e.stopPropagation();
                mediaActions.openContextMenu(current, {
                  x: e.clientX,
                  y: e.clientY,
                });
              }}
              className="max-h-full max-w-full animate-in zoom-in-95 duration-200"
            />
          )}

          {/* Prev / next chevrons.

              The hit area is a full-height strip, not just the round button:
              paging through a set should not demand pixel-accurate aim. The
              strip keeps pointer-events while the pointer is over it (see
              chromeHover), so a rapid click-click-click never falls through to
              the backdrop. */}
          {count > 1 && (
            <>
              <div
                {...chromeHover}
                className="absolute inset-y-0 left-0 z-10 flex w-20 items-center justify-start pl-3"
              >
                <button
                  type="button"
                  onClick={() => goTo(-1)}
                  aria-label="Previous (Left arrow)"
                  title="Previous (←)"
                  className={`rounded-full bg-black/50 p-2 text-zinc-200 transition-opacity duration-300 hover:bg-black/70 hover:text-white ${
                    chromeVisible
                      ? "opacity-100"
                      : "pointer-events-none opacity-0"
                  }`}
                >
                  <ChevronLeft className="h-6 w-6" />
                </button>
              </div>
              <div
                {...chromeHover}
                className="absolute inset-y-0 right-0 z-10 flex w-20 items-center justify-end pr-3"
              >
                <button
                  type="button"
                  onClick={() => goTo(1)}
                  aria-label="Next (Right arrow)"
                  title="Next (→)"
                  className={`rounded-full bg-black/50 p-2 text-zinc-200 transition-opacity duration-300 hover:bg-black/70 hover:text-white ${
                    chromeVisible
                      ? "opacity-100"
                      : "pointer-events-none opacity-0"
                  }`}
                >
                  <ChevronRight className="h-6 w-6" />
                </button>
              </div>
            </>
          )}

          {/* Zoom controls (images only) */}
          {current.kind === "image" && (
            <div
              {...chromeHover}
              className={`absolute bottom-3 left-1/2 z-10 flex -translate-x-1/2 items-center gap-0.5 rounded-lg bg-black/60 px-1 py-0.5 transition-opacity duration-300 ${
                chromeVisible ? "opacity-100" : "pointer-events-none opacity-0"
              }`}
            >
              <BarButton
                onClick={() => zoomToward(viewRef.current.scale / 1.3)}
                label="Zoom out"
                title="Zoom out (−)"
              >
                <Minus className="h-4 w-4" />
              </BarButton>
              <span className="w-12 text-center text-[11px] tabular-nums text-zinc-300">
                {zoomPercent}%
              </span>
              <BarButton
                onClick={() => zoomToward(viewRef.current.scale * 1.3)}
                label="Zoom in"
                title="Zoom in (+)"
              >
                <Plus className="h-4 w-4" />
              </BarButton>
              <BarButton
                onClick={resetView}
                label="Reset zoom"
                title="Reset zoom (0)"
              >
                <RotateCcw className="h-4 w-4" />
              </BarButton>
              <BarButton
                onClick={toggleFullscreen}
                label={isFullscreen ? "Exit fullscreen" : "Enter fullscreen"}
                title="Fullscreen (F)"
              >
                <Maximize2 className="h-4 w-4" />
              </BarButton>
            </div>
          )}
        </div>

        {/* Info panel.

            Every text block wraps. The params dump excludes the prompt (which
            the engine records inside the pipeline kwargs) — printing it there
            as one unbreakable JSON line is what used to blow this 288px panel
            out to ten page-widths of horizontal scroll. */}
        {showInfo && (
          <div
            {...chromeHover}
            className="w-80 shrink-0 space-y-3 overflow-y-auto overflow-x-hidden border-l border-white/10 bg-zinc-950/90 p-4 pt-14 animate-in slide-in-from-right duration-200"
          >
            <div className="min-w-0 space-y-1">
              <div className="flex items-center justify-between gap-2">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
                  Prompt
                </p>
                {current.prompt && (
                  <CopyButton
                    value={current.prompt}
                    label="Copy prompt"
                    className="text-zinc-400 hover:text-white"
                  />
                )}
              </div>
              <p className="whitespace-pre-wrap break-words text-xs leading-relaxed text-zinc-200">
                {current.prompt || "(no prompt)"}
              </p>
            </div>

            {current.negativePrompt && (
              <div className="min-w-0 space-y-1">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
                    Negative prompt
                  </p>
                  <CopyButton
                    value={current.negativePrompt}
                    label="Copy negative prompt"
                    className="text-zinc-400 hover:text-white"
                  />
                </div>
                <p className="whitespace-pre-wrap break-words text-xs leading-relaxed text-zinc-300">
                  {current.negativePrompt}
                </p>
              </div>
            )}

            {current.modelId && (
              <div className="min-w-0 space-y-1">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
                  Model
                </p>
                <p className="break-words font-mono text-[11px] text-zinc-300">
                  {current.modelId}
                </p>
              </div>
            )}

            {typeof current.seed === "number" && (
              <div className="space-y-1">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
                  Seed
                </p>
                <div className="flex items-center gap-2 rounded-md border border-white/10 bg-white/5 px-2 py-1">
                  <span className="min-w-0 flex-1 break-all font-mono text-xs tabular-nums text-zinc-100">
                    {current.seed}
                  </span>
                  <CopyButton
                    value={String(current.seed)}
                    label="Copy seed"
                    className="text-zinc-400 hover:text-white"
                  />
                  <button
                    type="button"
                    onClick={() => mediaActions.reuseSeed(current)}
                    className="shrink-0 text-xs text-violet-400 hover:underline"
                    title="Put this seed into the seed input to reproduce this result"
                  >
                    Reuse
                  </button>
                </div>
              </div>
            )}

            {metaJson && (
              <div className="min-w-0 space-y-1">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
                    Details
                  </p>
                  <CopyButton
                    value={metaJson}
                    label="Copy details JSON"
                    className="text-zinc-400 hover:text-white"
                  />
                </div>
                <pre className="max-h-64 overflow-y-auto overflow-x-hidden whitespace-pre-wrap break-words rounded-md border border-white/10 bg-white/5 p-2 font-mono text-[10px] leading-snug text-zinc-300">
                  {metaJson}
                </pre>
              </div>
            )}

            <button
              type="button"
              onClick={() => mediaActions.info(current)}
              className="w-full rounded-md border border-white/10 bg-white/5 px-2 py-1.5 text-xs text-zinc-300 transition-colors hover:bg-white/10 hover:text-white"
            >
              Open full metadata
            </button>
          </div>
        )}
      </div>

      {/* Hidden neighbor preloads */}
      {neighbors.map((url) => (
        <img key={url} src={url} alt="" aria-hidden className="hidden" />
      ))}
    </div>
  );

  return createPortal(overlay, document.body);
}
