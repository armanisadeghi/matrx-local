import {
  forwardRef,
  useCallback,
  useEffect,
  useRef,
  useState,
  type ComponentProps,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { GripHorizontal } from "lucide-react";

import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

export const PROMPT_TEXTAREA_DEFAULT_ROWS = 10;
export const NEGATIVE_PROMPT_DEFAULT_ROWS = 6;
export const PROMPT_TEXTAREA_MIN_ROWS = 5;

const ROW_HEIGHT_PX = 24;
const TEXTAREA_CHROME_PX = 18;
const MAX_HEIGHT_PX = 1200;
const STORAGE_PREFIX = "matrx-media-prompt-height:";

export const PROMPT_TEXTAREA_KEYS = {
  imageMain: "image-main",
  imageNegative: "image-negative",
  videoMain: "video-main",
  videoNegative: "video-negative",
  galleryImageMain: "gallery-image-main",
  galleryVideoMain: "gallery-video-main",
  savedPromptMain: "saved-prompt-main",
  variationTemplateMain: "variation-template-main",
  variationItemMain: "variation-item-main",
  variationItemNegative: "variation-item-negative",
  matrixMain: "matrix-main",
  matrixNegative: "matrix-negative",
  listOptions: "list-options",
  listQuickPaste: "list-quick-paste",
  listImport: "list-import",
} as const;

function heightForRows(rows: number): number {
  return Math.max(1, Math.trunc(rows)) * ROW_HEIGHT_PX + TEXTAREA_CHROME_PX;
}

function clampHeight(height: number, minHeight: number): number {
  return Math.min(MAX_HEIGHT_PX, Math.max(minHeight, Math.round(height)));
}

function storageKeyFor(key: string): string {
  return `${STORAGE_PREFIX}${key}`;
}

function readHeight(key: string, fallback: number, minHeight: number): number {
  try {
    const raw = localStorage.getItem(storageKeyFor(key));
    if (raw === null) return fallback;
    const parsed = Number.parseInt(raw, 10);
    return Number.isFinite(parsed)
      ? clampHeight(parsed, minHeight)
      : fallback;
  } catch {
    return fallback;
  }
}

function writeHeight(key: string, height: number): void {
  try {
    localStorage.setItem(storageKeyFor(key), String(Math.round(height)));
  } catch {
    // Resizing still works for this session when storage is unavailable.
  }
}

export interface PersistentTextareaResize {
  height: number;
  minHeight: number;
  maxHeight: number;
  reset: () => void;
  handleProps: {
    onPointerDown: (event: ReactPointerEvent<HTMLDivElement>) => void;
    onPointerMove: (event: ReactPointerEvent<HTMLDivElement>) => void;
    onPointerUp: (event: ReactPointerEvent<HTMLDivElement>) => void;
    onPointerCancel: (event: ReactPointerEvent<HTMLDivElement>) => void;
    onKeyDown: (event: ReactKeyboardEvent<HTMLDivElement>) => void;
  };
}

/**
 * Canonical persistent vertical resizing for media prompt editors.
 *
 * The same hook also powers the prompt-matrix highlight editor, whose mirror
 * requires custom markup and cannot consume ResizablePromptTextarea directly.
 */
export function usePersistentTextareaResize({
  storageKey,
  defaultRows = PROMPT_TEXTAREA_DEFAULT_ROWS,
  minRows = PROMPT_TEXTAREA_MIN_ROWS,
}: {
  storageKey: string;
  defaultRows?: number;
  minRows?: number;
}): PersistentTextareaResize {
  const defaultHeight = heightForRows(defaultRows);
  const minHeight = heightForRows(minRows);
  const [height, setHeight] = useState(() =>
    readHeight(storageKey, defaultHeight, minHeight),
  );
  const heightRef = useRef(height);
  const dragRef = useRef<{
    pointerId: number;
    startY: number;
    startHeight: number;
  } | null>(null);

  heightRef.current = height;

  useEffect(() => {
    const next = readHeight(storageKey, defaultHeight, minHeight);
    heightRef.current = next;
    setHeight(next);
  }, [defaultHeight, minHeight, storageKey]);

  const commit = useCallback(
    (next: number) => {
      const clamped = clampHeight(next, minHeight);
      heightRef.current = clamped;
      setHeight(clamped);
      writeHeight(storageKey, clamped);
    },
    [minHeight, storageKey],
  );

  const reset = useCallback(() => {
    commit(defaultHeight);
  }, [commit, defaultHeight]);

  const onPointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      event.currentTarget.setPointerCapture(event.pointerId);
      dragRef.current = {
        pointerId: event.pointerId,
        startY: event.clientY,
        startHeight: heightRef.current,
      };
    },
    [],
  );

  const onPointerMove = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      const drag = dragRef.current;
      if (drag === null || drag.pointerId !== event.pointerId) return;
      const next = clampHeight(
        drag.startHeight + event.clientY - drag.startY,
        minHeight,
      );
      heightRef.current = next;
      setHeight(next);
    },
    [minHeight],
  );

  const finishPointer = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      const drag = dragRef.current;
      if (drag === null || drag.pointerId !== event.pointerId) return;
      dragRef.current = null;
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId);
      }
      commit(heightRef.current);
    },
    [commit],
  );

  const onKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      if (event.key === "Home") {
        event.preventDefault();
        reset();
        return;
      }
      if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
      event.preventDefault();
      const direction = event.key === "ArrowDown" ? 1 : -1;
      const step = event.shiftKey ? ROW_HEIGHT_PX * 3 : ROW_HEIGHT_PX;
      commit(heightRef.current + direction * step);
    },
    [commit, reset],
  );

  return {
    height,
    minHeight,
    maxHeight: MAX_HEIGHT_PX,
    reset,
    handleProps: {
      onPointerDown,
      onPointerMove,
      onPointerUp: finishPointer,
      onPointerCancel: finishPointer,
      onKeyDown,
    },
  };
}

export function TextareaResizeHandle({
  resize,
  className,
}: {
  resize: PersistentTextareaResize;
  className?: string;
}) {
  return (
    <div
      role="separator"
      aria-label="Resize prompt editor"
      aria-orientation="horizontal"
      aria-valuemin={resize.minHeight}
      aria-valuemax={resize.maxHeight}
      aria-valuenow={resize.height}
      tabIndex={0}
      title="Drag to resize. Double-click or press Home to reset."
      onDoubleClick={resize.reset}
      {...resize.handleProps}
      className={cn(
        "-mt-px flex h-5 w-full touch-none cursor-row-resize items-center justify-center rounded-b-md border border-input bg-muted/35 text-muted-foreground/60 transition-colors",
        "hover:bg-muted hover:text-muted-foreground focus-visible:z-10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        className,
      )}
    >
      <GripHorizontal className="h-4 w-5" aria-hidden="true" />
    </div>
  );
}

export interface ResizablePromptTextareaProps
  extends ComponentProps<typeof Textarea> {
  resizeStorageKey: string;
  defaultRows?: number;
  minRows?: number;
  containerClassName?: string;
  handleClassName?: string;
}

/** Roomy prompt textarea with a persistent, full-width resize grip. */
export const ResizablePromptTextarea = forwardRef<
  HTMLTextAreaElement,
  ResizablePromptTextareaProps
>(function ResizablePromptTextarea(
  {
    resizeStorageKey,
    defaultRows = PROMPT_TEXTAREA_DEFAULT_ROWS,
    minRows = PROMPT_TEXTAREA_MIN_ROWS,
    containerClassName,
    handleClassName,
    className,
    style,
    rows: _rows,
    ...props
  },
  ref,
) {
  const resize = usePersistentTextareaResize({
    storageKey: resizeStorageKey,
    defaultRows,
    minRows,
  });
  const textareaStyle: CSSProperties = {
    ...style,
    height: `${resize.height}px`,
  };

  return (
    <div className={cn("w-full", containerClassName)}>
      <Textarea
        ref={ref}
        {...props}
        style={textareaStyle}
        className={cn(
          "min-h-0 resize-none rounded-b-none",
          className,
        )}
      />
      <TextareaResizeHandle
        resize={resize}
        {...(handleClassName !== undefined ? { className: handleClassName } : {})}
      />
    </div>
  );
});
