import { describe, expect, it, vi } from "vitest";

import {
  parseAIDreamStream,
  type AIDreamStreamProtocolIssue,
} from "@/lib/aidream-stream";
import type { TypedStreamEvent } from "@/types/python-generated/stream-events";

const encoder = new TextEncoder();

function responseFromTextChunks(chunks: string[]): Response {
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    }),
  );
}

async function collect(
  response: Response,
  options?: Parameters<typeof parseAIDreamStream>[1],
): Promise<TypedStreamEvent[]> {
  const events: TypedStreamEvent[] = [];
  for await (const event of parseAIDreamStream(response, options)) {
    events.push(event);
  }
  return events;
}

describe("parseAIDreamStream", () => {
  it("recovers after malformed input and surfaces the exact line", async () => {
    const issues: AIDreamStreamProtocolIssue[] = [];

    const events = await collect(
      responseFromTextChunks([
        '{"event":"chunk","data":{"text":"before"}}\n',
        "not-json\n",
        '{"event":"chunk","data":{"text":"after"}}\n',
      ]),
      { onProtocolIssue: (issue) => issues.push(issue) },
    );

    expect(events).toEqual([
      { event: "chunk", data: { text: "before" } },
      { event: "chunk", data: { text: "after" } },
    ]);
    expect(issues).toHaveLength(1);
    expect(issues[0]).toMatchObject({
      kind: "malformed-line",
      detail: { line: "not-json" },
    });
  });

  it("normalizes compact reasoning without mixing it into answer chunks", async () => {
    await expect(
      collect(responseFromTextChunks(['{"e":"r","t":"private thought"}\n'])),
    ).resolves.toEqual([
      { event: "reasoning_chunk", data: { text: "private thought" } },
    ]);
  });

  it("preserves a code point split across UTF-8 byte chunks", async () => {
    const bytes = encoder.encode('{"e":"c","t":"café"}\n');
    const split = bytes.findIndex((byte) => byte > 127) + 1;
    const response = new Response(
      new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(bytes.slice(0, split));
          controller.enqueue(bytes.slice(split));
          controller.close();
        },
      }),
    );

    await expect(collect(response)).resolves.toEqual([
      { event: "chunk", data: { text: "café" } },
    ]);
  });

  it("assembles partial JSON framing and flushes valid trailing input", async () => {
    await expect(
      collect(
        responseFromTextChunks([
          '{"event":"chunk","data":{"te',
          'xt":"fragmented"}}\n{"event":"end",',
          '"data":{}}',
        ]),
      ),
    ).resolves.toEqual([
      { event: "chunk", data: { text: "fragmented" } },
      { event: "end", data: {} },
    ]);
  });

  it("cancels the package reader without turning user abort into failure", async () => {
    let cancelledWith: unknown;
    let sent = false;
    const response = new Response(
      new ReadableStream<Uint8Array>({
        pull(controller) {
          if (!sent) {
            sent = true;
            controller.enqueue(encoder.encode('{"e":"c","t":"before"}\n'));
          }
        },
        cancel(reason) {
          cancelledWith = reason;
        },
      }),
    );
    const abort = new AbortController();
    const iterator = parseAIDreamStream(response, { signal: abort.signal });

    expect((await iterator.next()).value).toEqual({
      event: "chunk",
      data: { text: "before" },
    });
    const reason = new Error("user cancelled");
    abort.abort(reason);

    await expect(iterator.next()).resolves.toEqual({
      value: undefined,
      done: true,
    });
    expect(cancelledWith).toBe(reason);
  });

  it("delivers complete frames before surfacing a partial transport failure", async () => {
    const failure = new Error("socket reset after bytes");
    let pullCount = 0;
    const response = new Response(
      new ReadableStream<Uint8Array>(
        {
          pull(controller) {
            pullCount += 1;
            if (pullCount === 1) {
              controller.enqueue(encoder.encode('{"e":"c","t":"kept"}\n'));
              return;
            }
            controller.error(failure);
          },
        },
        { highWaterMark: 0 },
      ),
    );
    const iterator = parseAIDreamStream(response);

    await expect(iterator.next()).resolves.toEqual({
      value: { event: "chunk", data: { text: "kept" } },
      done: false,
    });
    await expect(iterator.next()).rejects.toBe(failure);
  });

  it("reports unknown valid JSON instead of silently treating it as an event", async () => {
    const onProtocolIssue = vi.fn();

    await expect(
      collect(responseFromTextChunks(['{"other":true}\n']), {
        onProtocolIssue,
      }),
    ).resolves.toEqual([]);
    expect(onProtocolIssue).toHaveBeenCalledWith({
      kind: "unknown-envelope",
      detail: { other: true },
    });
  });
});
