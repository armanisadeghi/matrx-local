/**
 * THE FLOOR — render ANY JSON value as something a human reads.
 *
 * This is the `renderValue` seam of `@ai-matrx/content-ir-react`, and the
 * "generic structured fallback" the consumer guide calls a PRODUCT
 * REQUIREMENT, not a temporary error state (§4): it is what makes broad
 * adoption safe while custom components propagate.
 *
 * 🚨 Deliberately NOT a JSON tree. Our reader is a non-technical Subject
 * Matter Expert; keys become headings, prose renders as prose, and the raw
 * object stays reachable behind a collapsed escape hatch — never the first
 * thing anyone sees.
 */

import { useState } from "react";
import { ChevronRight } from "lucide-react";
import { MessageMarkdown } from "@/components/chat/MessageMarkdown";
import { cn } from "@/lib/utils";

/** The discriminator is IDENTITY, not a data field — a label, never a row. */
const KIND_KEY = "__kind";

function humanize(key: string): string {
  return key
    .replace(/[_-]+/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/^./, (c) => c.toUpperCase());
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function ValueBody({ value }: { value: unknown }) {
  if (Array.isArray(value)) {
    if (value.length === 0) {
      return <span className="text-xs italic text-muted-foreground">empty</span>;
    }
    return (
      <ul className="space-y-2">
        {value.map((entry, i) => (
          <li
            key={i}
            className={cn(isPlainObject(entry) && "rounded-md border border-border/70 px-3 py-2")}
          >
            <ValueBody value={entry} />
          </li>
        ))}
      </ul>
    );
  }

  if (isPlainObject(value)) {
    const entries = Object.entries(value).filter(([key]) => key !== KIND_KEY);
    if (entries.length === 0) {
      return <span className="text-xs italic text-muted-foreground">empty</span>;
    }
    return (
      <div className="space-y-2">
        {entries.map(([key, entry]) => (
          <div key={key}>
            <div className="text-[0.6875rem] font-medium uppercase tracking-wide text-muted-foreground">
              {humanize(key)}
            </div>
            <div className="mt-0.5">
              <ValueBody value={entry} />
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (typeof value === "string") {
    // Long-form strings are the common case for AI output and are markdown far
    // more often than not; short ones cost nothing to route the same way.
    return (
      <div className="chat-prose text-[0.875rem] leading-[1.6]">
        <MessageMarkdown text={value} />
      </div>
    );
  }
  if (value === null || value === undefined) {
    return <span className="text-xs italic text-muted-foreground">none</span>;
  }
  return <span className="text-[0.875rem]">{String(value)}</span>;
}

export interface StructuredValueProps {
  value: unknown;
  /** The kind slug this value claims. An honesty line — never a renderer choice. */
  kind?: string;
  /** Why this shape has no custom view, in human words. */
  note?: string;
  /** Show the "what this is / raw data" footer. Default true. */
  footer?: boolean;
}

export function StructuredValue({ value, kind, note, footer = true }: StructuredValueProps) {
  const [rawOpen, setRawOpen] = useState(false);

  return (
    <div className="space-y-3">
      <ValueBody value={value} />

      {footer && (
        <div className="space-y-1 border-t border-border/70 pt-2 text-[0.6875rem] text-muted-foreground">
          {note && <p>{note}</p>}
          <div className="flex items-center gap-3">
            {kind && <span className="font-mono">{kind}</span>}
            <button
              type="button"
              onClick={() => setRawOpen((open) => !open)}
              className="flex items-center gap-0.5 hover:text-foreground"
            >
              <ChevronRight
                className={cn("h-3 w-3 transition-transform", rawOpen && "rotate-90")}
              />
              Raw data
            </button>
          </div>
          {rawOpen && (
            <pre className="max-h-80 overflow-auto rounded-md bg-muted p-3 font-mono text-[0.6875rem] leading-snug">
              {JSON.stringify(value, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
