/**
 * `flashcard_set` — the study deck.
 *
 * A desktop window has room for a grid, so cards sit side by side; each still
 * hides its back until clicked, because a flashcard whose answer is already
 * visible has stopped being a flashcard.
 *
 * The three card `$defs` (flashcard / enhanced_flashcard / tiered_flashcard)
 * differ only in optional fields, so `front` + `back` is read structurally and
 * everything else is left alone.
 */

import { useState } from "react";
import type { KindComponentProps } from "./types";

interface Card {
  front: string;
  back: string;
}

function readSet(value: unknown): { title: string; cards: Card[] } {
  if (typeof value !== "object" || value === null) return { title: "", cards: [] };
  const root = value as Record<string, unknown>;
  const rawTitle = root.title ?? root.set_title;
  const list = Array.isArray(root.cards) ? root.cards : [];

  const cards: Card[] = [];
  for (const entry of list) {
    if (typeof entry !== "object" || entry === null) continue;
    const card = entry as Record<string, unknown>;
    const front = typeof card.front === "string" ? card.front : "";
    // `back` is explicitly nullable in the schema — a card mid-stream may have
    // a front and no back yet, and it still deserves to render.
    const back = typeof card.back === "string" ? card.back : "";
    if (!front) continue;
    cards.push({ front, back });
  }

  return { title: typeof rawTitle === "string" ? rawTitle : "", cards };
}

function FlashcardTile({ card }: { card: Card }) {
  const [revealed, setRevealed] = useState(false);
  return (
    <button
      type="button"
      onClick={() => setRevealed((r) => !r)}
      aria-expanded={revealed}
      className="flex min-h-[6rem] flex-col rounded-lg border border-border/70 bg-card px-3 py-2.5 text-left transition-colors hover:border-border hover:bg-muted/30"
    >
      <span className="text-sm font-medium">{card.front}</span>
      {revealed ? (
        <span className="mt-2 text-[0.8125rem] leading-relaxed text-muted-foreground">
          {card.back || "No answer on this card yet."}
        </span>
      ) : (
        <span className="mt-auto pt-2 text-[0.6875rem] uppercase tracking-wide text-muted-foreground">
          Show answer
        </span>
      )}
    </button>
  );
}

export function FlashcardSetKind({ value, complete }: KindComponentProps) {
  const { title, cards } = readSet(value);

  if (cards.length === 0) {
    return (
      <div className="rounded-lg border border-border/70 bg-muted/20 px-3 py-2 text-sm text-muted-foreground">
        {complete ? "This deck has no cards." : "Building the deck…"}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm font-medium">{title || "Flashcards"}</span>
        <span className="text-xs tabular-nums text-muted-foreground">{cards.length} cards</span>
      </div>
      <div className="grid gap-2 [grid-template-columns:repeat(auto-fit,minmax(15rem,1fr))]">
        {cards.map((card, i) => (
          <FlashcardTile key={`${card.front}-${i}`} card={card} />
        ))}
      </div>
    </div>
  );
}
