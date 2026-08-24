/**
 * `markdown` — a kind whose payload is one string of markdown.
 *
 * The floor could render this, but a real markdown renderer is HOST property,
 * which is exactly why `renderValue` is a seam and this is a registered
 * component: the same `MessageMarkdown` the chat itself uses.
 */

import { MessageMarkdown } from "@/components/chat/MessageMarkdown";
import type { KindComponentProps } from "./types";

function textOf(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "object" && value !== null) {
    const text = (value as { text?: unknown }).text;
    if (typeof text === "string") return text;
  }
  return "";
}

export function MarkdownKind({ value }: KindComponentProps) {
  const text = textOf(value);
  if (!text) return null;
  return (
    <div className="chat-prose text-[0.9375rem] leading-[1.7]">
      <MessageMarkdown text={text} />
    </div>
  );
}
