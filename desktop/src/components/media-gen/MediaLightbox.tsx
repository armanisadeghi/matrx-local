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
  Check,
  ChevronLeft,
  ChevronRight,
  Copy,
  Download,
  Info,
  Maximize,
  Maximize2,
  Minimize,
  Minus,
  Plus,
  RotateCcw,
  Sprout,
  Trash2,
  X,
} from "lucide-react";

// ── Frozen contract ──────────────────────────────────────────────────────────

export interface LightboxItem {
  id: string;
  kind: "image" | "video";
  /** Object/blob URL ready for <img>/<video>. */
  url: string;
  prompt?: string;
  seed?: number | null;
  /** Arbitrary metadata shown in the info panel (params JSON etc.). */
  meta?: Record<string, unknown>;
  title?: string;
}

// ── Internals ────────────────────────────────────────────────────────────────

const MIN_SCALE = 0.25;
const MAX_SCALE = 12;
const BAR_HIDE_MS = 2000;

function clampScale(s: number): number {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, s));
}

/** Sensible download filename from the item's title/prompt/id. */
function downloadName(item: LightboxItem): string {
  const base =
    (item.title || item.prompt || item.id)
      .trim()
      .slice(0, 48)
      .replace(/[^a-zA-Z0-9-_ ]+/g, "")
      .replace(/\s+/g, "-")
      .replace(/^-+|-+$/g, "") || "matrx-media";
  const ext = item.kind === "video" ? "mp4" : "png";
  return `${base}.${ext}`;
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

/** Small copy-to-clipboard button with a transient "copied" check. */
function CopyValueButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        void navigator.clipboard.writeText(value).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        });
      }}
      aria-label={label}
      title={label}
      className="text-zinc-400 hover:text-white transition-colors"
    >
      {copied ? (
        <Check className="h-3.5 w-3.5 text-green-400" />
      ) : (
        <Copy className="h-3.5 w-3.5" />
      )}
    </button>
  );
}

// ── Component ────────────────────────────────────────────────────────────────

export function MediaLightbox({
  open,
  items,
  startIndex = 0,
  onClose,
  onReuseSeed,
  onDelete,
}: {
  open: boolean;
  items: LightboxItem[];
  startIndex?: number;
  onClose: () => void;
  onReuseSeed?: (seed: number) => void;
  onDelete?: (id: string) => void;
}): JSX.Element | null {
  const [index, setIndex] = useState(0);
  const [scale, setScale] = useState(1);
  const [tx, setTx] = useState(0);
  const [ty, setTy] = useState(0);
  const [showInfo, setShowInfo] = useState(false);
  const [barVisible, setBarVisible] = useState(true);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [dragging, setDragging] = useState(false);

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
    setConfirmingDelete(false);
    setBarVisible(true);
    // items.length intentionally read once at open — the viewing set is
    // snapshotted by the caller; live length changes are clamped via safeIndex.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, startIndex]);

  const goTo = useCallback(
    (dir: 1 | -1) => {
      if (count < 2) return;
      setIndex((i) => (i + dir + count) % count);
      setScale(1);
      setTx(0);
      setTy(0);
      setConfirmingDelete(false);
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
          if (confirmingDelete) setConfirmingDelete(false);
          else onClose();
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
  }, [open, confirmingDelete, onClose, goTo, zoomToward, resetView, toggleFullscreen]);

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

  const holdBar = useCallback(() => {
    if (barTimerRef.current !== null) {
      window.clearTimeout(barTimerRef.current);
      barTimerRef.current = null;
    }
    setBarVisible(true);
  }, []);

  const handleDownload = useCallback(() => {
    if (!current) return;
    const a = document.createElement("a");
    a.href = current.url;
    a.download = downloadName(current);
    a.click();
  }, [current]);

  const handleDelete = useCallback(() => {
    if (!current || !onDelete) return;
    if (!confirmingDelete) {
      setConfirmingDelete(true);
      return;
    }
    const deletedId = current.id;
    setConfirmingDelete(false);
    onDelete(deletedId);
    if (count <= 1) {
      onClose();
    } else {
      // Stay on the same slot; safeIndex clamps once the parent removes it.
      setIndex((i) => Math.min(i, count - 2));
      resetView();
    }
  }, [current, onDelete, confirmingDelete, count, onClose, resetView]);

  // Neighbor preload (images only).
  const neighbors = useMemo(() => {
    if (count < 2) return [] as string[];
    const urls: string[] = [];
    for (const off of [1, count - 1]) {
      const item = items[(safeIndex + off) % count];
      if (item.kind === "image") urls.push(item.url);
    }
    return [...new Set(urls)];
  }, [items, safeIndex, count]);

  const metaJson = useMemo(() => {
    if (!current?.meta || Object.keys(current.meta).length === 0) return null;
    return JSON.stringify(current.meta, null, 2);
  }, [current?.meta]);

  if (!open || !current) return null;

  const chromeVisible = barVisible || showInfo || confirmingDelete || dragging;
  const heading = current.title || current.prompt || "";
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
        onMouseEnter={holdBar}
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
          <BarButton onClick={handleDownload} label="Download" title="Download">
            <Download className="h-4 w-4" />
          </BarButton>
          {onReuseSeed && typeof current.seed === "number" && (
            <BarButton
              onClick={() => onReuseSeed(current.seed as number)}
              label="Reuse seed"
              title={`Reuse seed ${current.seed}`}
            >
              <Sprout className="h-4 w-4" />
            </BarButton>
          )}
          {onDelete &&
            (confirmingDelete ? (
              <span className="flex items-center gap-1 rounded-md bg-red-500/15 px-1.5 py-0.5">
                <span className="text-[11px] font-medium text-red-300">
                  Delete?
                </span>
                <BarButton
                  onClick={handleDelete}
                  label="Confirm delete"
                  danger
                >
                  <Check className="h-4 w-4" />
                </BarButton>
                <BarButton
                  onClick={() => setConfirmingDelete(false)}
                  label="Cancel delete"
                >
                  <X className="h-4 w-4" />
                </BarButton>
              </span>
            ) : (
              <BarButton onClick={handleDelete} label="Delete" danger>
                <Trash2 className="h-4 w-4" />
              </BarButton>
            ))}
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
            // Backdrop click closes — only the stage itself, never the media.
            if (e.target === e.currentTarget) onClose();
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
              className="max-h-full max-w-full animate-in zoom-in-95 duration-200"
            />
          )}

          {/* Prev / next chevrons */}
          {count > 1 && (
            <>
              <button
                type="button"
                onClick={() => goTo(-1)}
                onMouseEnter={holdBar}
                aria-label="Previous (Left arrow)"
                title="Previous (←)"
                className={`absolute left-3 top-1/2 z-10 -translate-y-1/2 rounded-full bg-black/50 p-2 text-zinc-200 transition-opacity duration-300 hover:bg-black/70 hover:text-white ${
                  chromeVisible ? "opacity-100" : "pointer-events-none opacity-0"
                }`}
              >
                <ChevronLeft className="h-6 w-6" />
              </button>
              <button
                type="button"
                onClick={() => goTo(1)}
                onMouseEnter={holdBar}
                aria-label="Next (Right arrow)"
                title="Next (→)"
                className={`absolute right-3 top-1/2 z-10 -translate-y-1/2 rounded-full bg-black/50 p-2 text-zinc-200 transition-opacity duration-300 hover:bg-black/70 hover:text-white ${
                  chromeVisible ? "opacity-100" : "pointer-events-none opacity-0"
                }`}
              >
                <ChevronRight className="h-6 w-6" />
              </button>
            </>
          )}

          {/* Zoom controls (images only) */}
          {current.kind === "image" && (
            <div
              onMouseEnter={holdBar}
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

        {/* Info panel */}
        {showInfo && (
          <div className="w-72 shrink-0 space-y-3 overflow-y-auto border-l border-white/10 bg-zinc-950/90 p-4 pt-14 animate-in slide-in-from-right duration-200">
            <div className="space-y-1">
              <div className="flex items-center justify-between gap-2">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
                  Prompt
                </p>
                {current.prompt && (
                  <CopyValueButton
                    value={current.prompt}
                    label="Copy prompt"
                  />
                )}
              </div>
              <p className="whitespace-pre-wrap break-words text-xs leading-relaxed text-zinc-200">
                {current.prompt || "(no prompt)"}
              </p>
            </div>

            {typeof current.seed === "number" && (
              <div className="space-y-1">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
                  Seed
                </p>
                <div className="flex items-center gap-2 rounded-md border border-white/10 bg-white/5 px-2 py-1">
                  <span className="flex-1 font-mono text-xs tabular-nums text-zinc-100">
                    {current.seed}
                  </span>
                  <CopyValueButton
                    value={String(current.seed)}
                    label="Copy seed"
                  />
                  {onReuseSeed && (
                    <button
                      type="button"
                      onClick={() => onReuseSeed(current.seed as number)}
                      className="text-xs text-violet-400 hover:underline"
                      title="Put this seed into the seed input to reproduce this result"
                    >
                      Reuse
                    </button>
                  )}
                </div>
              </div>
            )}

            {metaJson && (
              <div className="space-y-1">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
                    Details
                  </p>
                  <CopyValueButton value={metaJson} label="Copy details JSON" />
                </div>
                <pre className="max-h-64 overflow-auto rounded-md border border-white/10 bg-white/5 p-2 font-mono text-[10px] leading-snug text-zinc-300">
                  {metaJson}
                </pre>
              </div>
            )}
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
