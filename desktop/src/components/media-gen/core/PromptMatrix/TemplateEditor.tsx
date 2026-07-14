/**
 * TemplateEditor — a textarea that highlights {{variable}} tokens as you type.
 *
 * The technique is the standard one: render a styled mirror div UNDER the
 * textarea, but let the textarea draw the real text. The lower layer paints
 * only token backgrounds, so the user gets native caret, selection, undo, IME,
 * spellcheck, and accessibility without the visible text drifting away from the
 * cursor.
 *
 * The mirror MUST match the textarea's font, padding, border and wrapping
 * exactly, or the highlight drifts from the text. That is why the geometry
 * classes live in one shared constant rather than being repeated.
 */

import { useCallback, useLayoutEffect, useRef } from "react";
import { Label } from "@/components/ui/label";
import { findTokens } from "@/lib/prompt-matrix";
import { cn } from "@/lib/utils";

/** Typography + box model shared by the textarea and its highlight mirror.
 *  Any change here MUST apply to both, or the highlights will drift. */
const SHARED_BOX =
  "w-full rounded-md border border-input px-3 py-2 text-sm leading-relaxed " +
  "font-normal tracking-normal whitespace-pre-wrap break-words";

export function TemplateEditor({
  label,
  value,
  onChange,
  placeholder,
  /** Names that have at least one option — used to flag unknown tokens. */
  knownVariables,
  minHeightClass = "min-h-[120px]",
  hint,
}: {
  label: string;
  value: string;
  onChange: (text: string) => void;
  placeholder?: string;
  knownVariables: ReadonlySet<string>;
  minHeightClass?: string;
  hint?: React.ReactNode;
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const mirrorRef = useRef<HTMLDivElement>(null);

  // Keep the mirror scrolled with the textarea, or long prompts desync.
  const syncScroll = useCallback(() => {
    const ta = textareaRef.current;
    const mirror = mirrorRef.current;
    if (ta === null || mirror === null) return;
    mirror.scrollTop = ta.scrollTop;
    mirror.scrollLeft = ta.scrollLeft;
  }, []);

  useLayoutEffect(syncScroll, [value, syncScroll]);

  const segments = buildSegments(value, knownVariables);

  return (
    <div className="space-y-1.5">
      <Label className="text-xs">{label}</Label>
      <div className="relative">
        <div
          ref={mirrorRef}
          aria-hidden="true"
          className={cn(
            SHARED_BOX,
            minHeightClass,
            "pointer-events-none absolute inset-0 overflow-hidden border-transparent text-transparent",
          )}
        >
          {segments.map((seg, i) =>
            seg.kind === "text" ? (
              <span key={i}>{seg.text}</span>
            ) : (
              <span
                key={i}
                className={cn(
                  "rounded-[3px]",
                  seg.known
                    ? "bg-primary/15 text-transparent"
                    : // An unknown token would generate a literal "{{style}}"
                      // into the image. Make it impossible to miss.
                      "bg-destructive/15 text-transparent shadow-[inset_0_-1px_0_hsl(var(--destructive))]",
                )}
              >
                {seg.text}
              </span>
            ),
          )}
          {/* A trailing newline needs a character or the mirror loses a line. */}
          {value.endsWith("\n") ? "​" : null}
        </div>
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onScroll={syncScroll}
          placeholder={placeholder}
          spellCheck
          className={cn(
            SHARED_BOX,
            minHeightClass,
            "relative resize-y bg-transparent text-foreground caret-foreground",
            "ring-offset-background placeholder:text-muted-foreground",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
            "disabled:cursor-not-allowed disabled:opacity-50",
            "selection:bg-primary/30",
          )}
        />
      </div>
      {hint}
    </div>
  );
}

type Segment =
  | { kind: "text"; text: string }
  | { kind: "token"; text: string; known: boolean };

function buildSegments(
  text: string,
  known: ReadonlySet<string>,
): Segment[] {
  const tokens = findTokens(text);
  if (tokens.length === 0) return [{ kind: "text", text }];

  const out: Segment[] = [];
  let cursor = 0;
  for (const tok of tokens) {
    if (tok.start > cursor) {
      out.push({ kind: "text", text: text.slice(cursor, tok.start) });
    }
    out.push({
      kind: "token",
      text: text.slice(tok.start, tok.end),
      known: known.has(tok.key),
    });
    cursor = tok.end;
  }
  if (cursor < text.length) {
    out.push({ kind: "text", text: text.slice(cursor) });
  }
  return out;
}
