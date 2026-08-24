/** @vitest-environment jsdom */

/**
 * THE END-TO-END PROOF that this app renders server-built kinds.
 *
 * 🚨 The fixture is NOT hand-written. `__fixtures__/server-render-blocks.json`
 * was produced by running aidream's PRODUCTION block processor over answer
 * text an agent really emits, against the LIVE `content_ir` schemas. A test
 * that passes on a hand-built envelope proves nothing — that is exactly how
 * the render-block channel stayed dead.
 *
 * Two halves are pinned:
 *   1. `StreamBlockBuilder` keeps a kind-carrying block AS A KIND instead of
 *      flattening it to markdown text, which is all it used to do.
 *   2. `KindBlockView` routes that block through the SHARED kind route to a
 *      bundled component, or to the honest generic floor.
 *
 * Only the catalog HTTP call is stubbed — the resolver is fed exactly the rows
 * `content_ir.kind_component` holds for `platform='desktop'`.
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { StreamBlockBuilder } from "@/lib/chat-blocks";
import type { RenderBlockPayload } from "@/types/python-generated/stream-events";
import { componentRegistry, kindRegistry } from "./runtime/registry";
import { KindBlockView } from "./render/KindBlockView";
import fixture from "./__fixtures__/server-render-blocks.json";

type FixtureBlock = RenderBlockPayload;
const BLOCKS = (fixture as { blocks: Record<string, FixtureBlock> }).blocks;

/** The LIVE rows, verbatim (migration 010_kind_component_desktop.sql). */
const ROWS = [
  { kind: "flashcard_set", componentKey: "flashcard_set_desktop" },
  { kind: "quiz_set", componentKey: "quiz_set_desktop" },
  { kind: "research_report", componentKey: "not_mapped_in_this_client" },
].map((row) => ({
  ...row,
  platform: "desktop",
  role: "output" as const,
  source: "bundled",
  config: {},
  isActive: true,
  componentSource: null,
  propsTransform: null,
  pinnedKindVersion: null,
  updatedAt: null,
  createdBy: null,
}));

beforeAll(() => {
  componentRegistry.replaceDbRows(ROWS);
  for (const kind of ["flashcard_set", "quiz_set", "research_report"]) {
    (kindRegistry as unknown as { known: Map<string, unknown> }).known.set(kind, {
      kind,
      schema: null,
      schemaSource: "content_ir",
      tier: "warm",
    });
  }
});

describe("StreamBlockBuilder keeps structured content structured", () => {
  it("makes a kind block, not a text block, for a stamped payload", () => {
    const builder = new StreamBlockBuilder();
    // `markdown` is what `renderBlockText` would have flattened this to. It is
    // passed on purpose: the builder must prefer the envelope over it.
    builder.applyRenderBlock(BLOCKS.flashcard_set!, "Front: …\nBack: …", false);
    const blocks = builder.snapshot();
    expect(blocks).toHaveLength(1);
    expect(blocks[0]!.type).toBe("kind");
    expect(blocks[0]).toMatchObject({ kind: "flashcard_set" });
  });

  it("still flattens a block that carries NO envelope", () => {
    const builder = new StreamBlockBuilder();
    const plain: RenderBlockPayload = {
      blockId: "b1",
      blockIndex: 0,
      type: "text",
      status: "complete",
      content: "just prose",
    };
    builder.applyRenderBlock(plain, "just prose", false);
    expect(builder.snapshot()[0]!.type).toBe("text");
  });

  it("never lets a replayed streaming frame downgrade a closed block", () => {
    const builder = new StreamBlockBuilder();
    builder.applyRenderBlock(BLOCKS.flashcard_set!, null, false);
    builder.applyRenderBlock(
      { ...BLOCKS.flashcard_set!, status: "streaming" },
      null,
      false,
    );
    const [block] = builder.snapshot();
    expect(block).toMatchObject({ type: "kind", complete: true });
  });
});

describe("KindBlockView draws server-built kinds", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
      .IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function draw(payload: RenderBlockPayload) {
    act(() =>
      root.render(
        <KindBlockView
          blockId={payload.blockId}
          type={payload.type}
          content={payload.content}
          metadata={payload.metadata}
          complete
        />,
      ),
    );
    return container.textContent ?? "";
  }

  it("renders a flashcard_set as a deck with the answers hidden", () => {
    const text = draw(BLOCKS.flashcard_set!);
    expect(text).toContain("What pigment absorbs light?");
    expect(text).toContain("Show answer");
    // A flashcard whose back is already visible has stopped being a flashcard.
    expect(text).not.toContain("Chlorophyll.");
  });

  it("renders a quiz_set as answerable choices with the answer withheld", () => {
    const text = draw(BLOCKS.quiz_set!);
    expect(text).toContain("Which pigment absorbs light?");
    expect(text).toContain("Carotene");
    expect(text).not.toContain("absorbs blue and red");
  });

  it("sends a KNOWN kind with no component here to the honest floor", () => {
    // The same real envelope, relabelled to a kind this app maps no component
    // for. R6's disposition: readable data plus a muted "no custom view" note
    // — never an error, never a blank block.
    const source = BLOCKS.flashcard_set!;
    const envelope = JSON.parse(
      JSON.stringify((source.metadata as Record<string, unknown>).__ir),
    ) as { root: { kind: string } };
    envelope.root.kind = "research_report";
    const text = draw({ ...source, metadata: { __ir: envelope } });
    expect(text.toLowerCase()).toContain("no custom view");
    expect(text).toContain("What pigment absorbs light?");
  });
});
