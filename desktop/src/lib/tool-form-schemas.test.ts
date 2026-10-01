import { describe, expect, it } from "vitest";
import engineSchemas from "./__fixtures__/engine-tool-schemas.json";
import { fromEngineSchema } from "./tool-registry";
import { schemaToZod } from "./schema-to-zod";
import type { ToolFieldSchema } from "@/types/tool-schema";

// The live engine catalog (kept fresh by
// tests/parity/test_tool_form_schema_fixture.py), run through the exact
// mapping + validation the Tools page uses.
const schemas = (engineSchemas as Parameters<typeof fromEngineSchema>[0][]).map(
  fromEngineSchema,
);

/** The values ToolForm starts with (mirror of its defaultValues memo). */
function startingValues(fields: ToolFieldSchema[]): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  for (const f of fields) {
    if (f.defaultValue !== undefined) values[f.name] = f.defaultValue;
    else if (f.type === "boolean") values[f.name] = false;
    else if (f.type === "tags") values[f.name] = [];
    else if (f.type === "key-value") values[f.name] = {};
  }
  return values;
}

/** A value a person could enter that the server would accept. */
function sampleValue(f: ToolFieldSchema): unknown {
  switch (f.type) {
    case "number": {
      const lo = f.exclusiveMin ?? f.min ?? 0;
      const hi = f.exclusiveMax ?? f.max ?? lo + 10;
      const mid = (lo + hi) / 2;
      return f.integer ? Math.ceil(mid) : mid;
    }
    case "boolean":
      return true;
    case "select":
      return f.options?.[0]?.value;
    case "tags":
      return f.itemType ? ["1"] : ["a"];
    case "key-value":
      return { a: "b" };
    case "json":
      return {};
    default:
      return "sample";
  }
}

function issuesFor(toolName: string, values: Record<string, unknown>) {
  const schema = schemas.find((s) => s.toolName === toolName)!;
  const r = schemaToZod(schema.fields).safeParse(values);
  return r.success ? [] : r.error.issues;
}

describe("tool forms built from the live engine catalog", () => {
  it("covers the catalog", () => {
    expect(schemas.length).toBeGreaterThan(100);
  });

  it("every tool submits with its defaults plus its required fields filled", () => {
    const failures: string[] = [];
    for (const s of schemas) {
      const values = startingValues(s.fields);
      for (const f of s.fields) {
        if (f.required && values[f.name] === undefined) values[f.name] = sampleValue(f);
      }
      const r = schemaToZod(s.fields).safeParse(values);
      if (!r.success) {
        for (const i of r.error.issues) failures.push(`${s.toolName}.${i.path.join(".")}: ${i.message}`);
      }
    }
    expect(failures).toEqual([]);
  });

  it("every shown default is a value the form accepts", () => {
    const failures: string[] = [];
    for (const s of schemas) {
      for (const f of s.fields) {
        if (f.defaultValue === undefined) continue;
        const r = schemaToZod([{ ...f, required: true }]).safeParse({ [f.name]: f.defaultValue });
        if (!r.success) failures.push(`${s.toolName}.${f.name} = ${JSON.stringify(f.defaultValue)}`);
      }
    }
    expect(failures).toEqual([]);
  });

  it("never carries a null default into the form", () => {
    const nullDefaults = schemas.flatMap((s) =>
      s.fields.filter((f) => f.defaultValue === null).map((f) => `${s.toolName}.${f.name}`),
    );
    expect(nullDefaults).toEqual([]);
  });

  it("renders every string enum as a dropdown of its values", () => {
    const missed: string[] = [];
    for (const raw of engineSchemas as { name: string; input_schema?: { properties?: Record<string, { enum?: unknown[] }> } }[]) {
      const ui = schemas.find((s) => s.toolName === raw.name)!;
      for (const [name, def] of Object.entries(raw.input_schema?.properties ?? {})) {
        if (!def.enum?.every((v) => typeof v === "string")) continue;
        const f = ui.fields.find((x) => x.name === name)!;
        if (f.type !== "select" || f.options?.map((o) => o.value).join() !== def.enum.join()) {
          missed.push(`${raw.name}.${name}`);
        }
      }
    }
    expect(missed).toEqual([]);
  });

  it("blank required fields say Required", () => {
    const issues = issuesFor("local_screen", {});
    const action = issues.find((i) => i.path[0] === "action");
    expect(action?.message).toBe("Required");
  });

  it("book capture accepts fractions and rejects the bounds the server rejects", () => {
    const base = { action: "capture_book" };
    expect(issuesFor("local_screen", { ...base, page_delay_seconds: 0.9, page_change_threshold: 0.001 })).toEqual([]);
    for (const bad of [0, 1]) {
      const issues = issuesFor("local_screen", { ...base, page_change_threshold: bad });
      expect(issues.map((i) => i.path[0])).toContain("page_change_threshold");
    }
  });

  it("whole-number fields refuse decimals; number lists refuse words", () => {
    const tabIndex = issuesFor("local_browser", { action: "tabs", tab_index: 1.5 });
    expect(tabIndex.map((i) => i.message)).toContain("Whole numbers only");
    const region = issuesFor("local_screen", { action: "screenshot", region: ["0", "x", "10", "10"] });
    expect(region.map((i) => i.message)).toContain("Whole numbers only");
  });

  it("hides engine-internal parameters", () => {
    const internal = schemas.flatMap((s) =>
      s.fields.filter((f) => f.name.startsWith("_")).map((f) => `${s.toolName}.${f.name}`),
    );
    expect(internal).toEqual([]);
  });

  it("gives list-or-mapping parameters a JSON box", () => {
    const labels = schemas.find((s) => s.toolName === "ExtractEntities")!.fields.find((f) => f.name === "labels")!;
    expect(labels.type).toBe("json");
  });
});

