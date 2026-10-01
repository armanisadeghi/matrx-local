import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { ContextReceipt } from "@ai-matrx/agents/context";
import {
  buildRequestContext,
  checkContextReceipt,
  describeContextReceipt,
  toContextReceipt,
} from "@/lib/request-context";

const PAGE = {
  key: "page_full_content",
  label: "Page content",
  origin: "page" as const,
  value: "x".repeat(5000),
};

function receipt(patch: Partial<ContextReceipt> = {}): ContextReceipt {
  return {
    version: 1,
    surface: null,
    cap: 50000,
    model_reads_context: true,
    rules_error: null,
    rows: [],
    ...patch,
  };
}

describe("buildRequestContext — the one door", () => {
  it("sends nothing when there is nothing", () => {
    expect(buildRequestContext().context).toBeUndefined();
    expect(buildRequestContext({ directives: { __google_files: [] } }).context).toBeUndefined();
  });

  it("ships the __google_files directive verbatim, never as an envelope", () => {
    const { rows, context } = buildRequestContext({
      directives: { __google_files: ["a", "b"] },
    });
    expect(rows).toEqual([]);
    expect(context).toEqual({ __google_files: ["a", "b"] });
  });

  it("refuses a directive key passed as a value", () => {
    expect(() =>
      buildRequestContext({
        sources: [{ key: "__google_files", label: "x", origin: "attached", value: ["a"] }],
      }),
    ).toThrow(/reserved directive/);
  });

  it("wraps values through buildContextWire and never sends an excluded one", () => {
    const sent = buildRequestContext({ sources: [PAGE] });
    expect(sent.context).toMatchObject({
      page_full_content: { content: PAGE.value, label: "Page content" },
    });
    const off = buildRequestContext({
      sources: [PAGE],
      savedRules: { _default: { page_full_content: { include: false } } },
    });
    expect(off.rows[0]?.include).toBe(false);
    expect(off.context).toBeUndefined();
  });
});

describe("context_receipt", () => {
  it("normalizes the generated wire shape", () => {
    const r = toContextReceipt({ cap: 50000, rows: [] });
    expect(r).toEqual(receipt());
  });

  it("is quiet when the server did what the rows said", () => {
    const { rows } = buildRequestContext({ sources: [PAGE] });
    const row = rows[0]!;
    const actual = receipt({
      rows: [
        {
          key: row.key,
          label: row.label,
          surface_key: "_default",
          origin: "client",
          chars: row.chars,
          include: row.include,
          max_inline_chars: row.max_inline_chars,
          delivery: row.delivery === "server" ? "on_request" : row.delivery,
          decided_by: row.decided_by,
          user_rule: null,
          clamped: false,
          client_sent_excluded: false,
          blocked_by: null,
        },
      ],
    });
    expect(checkContextReceipt(rows, actual)).toEqual([]);
    expect(describeContextReceipt(actual)).toBe("Context: 0 inline · 1 on request · 0 off.");
  });

  it("is loud on a missing row, a client-sent excluded value, and a rules error", () => {
    const { rows } = buildRequestContext({ sources: [PAGE] });
    const fields = checkContextReceipt(rows, receipt({ rules_error: "db down" })).map(
      (m) => m.field,
    );
    expect(fields).toEqual(["missing", "user_rule"]);
    const excluded = checkContextReceipt(
      [],
      receipt({
        rows: [
          {
            key: "page_full_content",
            label: "Page",
            surface_key: "_default",
            origin: "client",
            chars: 10,
            include: false,
            max_inline_chars: 200,
            delivery: "off",
            decided_by: { include: "you", max_inline_chars: "default" },
            user_rule: { include: false },
            clamped: false,
            client_sent_excluded: true,
            blocked_by: null,
          },
        ],
      }),
    );
    expect(excluded).toEqual([
      { key: "page_full_content", field: "include", expected: false, actual: "sent" },
    ]);
  });
});

/**
 * THE DOOR GUARD: no production file may put a `context` on a request body
 * except through `buildRequestContext`. The branded `RequestContextWire`
 * stops `buildCloudChatRequest` from taking a plain object; this scan stops a
 * second request builder from appearing beside it.
 */
describe("context single door", () => {
  const SRC = resolve(__dirname, "..");
  const ALLOWED = new Set([
    join(SRC, "lib", "request-context.ts"),
    join(SRC, "hooks", "use-cloud-chat.ts"),
  ]);
  function walk(dir: string, out: string[] = []): string[] {
    for (const name of readdirSync(dir)) {
      const path = join(dir, name);
      if (name === "node_modules" || name === "python-generated") continue;
      if (statSync(path).isDirectory()) walk(path, out);
      else if (/\.(ts|tsx)$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(path);
    }
    return out;
  }

  it("only the door builds a request context", () => {
    const offenders: string[] = [];
    for (const file of walk(SRC)) {
      if (ALLOWED.has(file)) continue;
      const text = readFileSync(file, "utf8");
      // A wire built anywhere else, or the directive set as an object key
      // anywhere else, is a second door.
      if (/buildContextWire\(|\[GOOGLE_FILES_CONTEXT_KEY\]\s*:|\b__google_files["']?\s*:/.test(text)) {
        offenders.push(file);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("use-cloud-chat sends context only from the branded door value", () => {
    const text = readFileSync(join(SRC, "hooks", "use-cloud-chat.ts"), "utf8");
    expect(text).toMatch(/context\?: RequestContextWire,/);
    expect(text).not.toMatch(/\bcontext:\s*\{/);
  });
});
