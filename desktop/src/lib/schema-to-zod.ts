import { z } from "zod";
import type { ToolFieldSchema } from "@/types/tool-schema";

/**
 * Converts an array of ToolFieldSchema definitions into a Zod object schema.
 * Used at runtime to validate tool form inputs before submission.
 */
/** Blank required inputs read "Required", not a type-mismatch message. */
const blankIsRequired = {
  error: (issue: { input?: unknown }) =>
    issue.input === undefined || issue.input === null ? "Required" : undefined,
};

export function schemaToZod(fields: ToolFieldSchema[]): z.ZodObject<Record<string, z.ZodTypeAny>> {
  const shape: Record<string, z.ZodTypeAny> = {};

  for (const field of fields) {
    let schema: z.ZodTypeAny;

    switch (field.type) {
      case "text":
      case "textarea":
      case "code":
      case "file-path": {
        let s = z.string(blankIsRequired);
        if (field.min != null) s = s.min(field.min);
        if (field.max != null) s = s.max(field.max);
        if (field.pattern) s = s.regex(new RegExp(field.pattern));
        // An empty box is stripped before sending, so it is the same as blank.
        if (field.required) s = s.min(Math.max(1, field.min ?? 1), "Required");
        schema = s;
        break;
      }

      case "number": {
        let n = z.number(blankIsRequired);
        if (field.integer) n = n.int("Whole numbers only");
        if (field.min != null) n = n.min(field.min);
        if (field.max != null) n = n.max(field.max);
        if (field.exclusiveMin != null) n = n.gt(field.exclusiveMin);
        if (field.exclusiveMax != null) n = n.lt(field.exclusiveMax);
        schema = n;
        break;
      }

      case "boolean":
        schema = z.boolean(blankIsRequired);
        break;

      case "select": {
        if (field.options && field.options.length > 0) {
          const values = field.options.map((o) => o.value) as [string, ...string[]];
          schema = z.enum(values, blankIsRequired);
        } else {
          schema = z.string();
        }
        break;
      }

      case "tags": {
        // Tags are typed as text; number arrays (e.g. a crop region) are
        // converted on submit, so each entry must parse.
        const item =
          field.itemType === "integer"
            ? z.string().regex(/^-?\d+$/, "Whole numbers only")
            : field.itemType === "number"
              ? z.string().refine((v) => v.trim() !== "" && Number.isFinite(Number(v)), "Numbers only")
              : z.string();
        schema = z.array(item, blankIsRequired);
        break;
      }

      case "key-value":
        schema = z.record(z.string(), z.string());
        break;

      case "json":
        // JsonField holds the raw text; it must parse before it is sent.
        schema = z.any().superRefine((v, ctx) => {
          const blank = v === undefined || v === null || (typeof v === "string" && v.trim() === "");
          if (blank) {
            if (field.required) ctx.addIssue({ code: "custom", message: "Required" });
            return;
          }
          if (typeof v !== "string") return;
          try {
            JSON.parse(v);
          } catch {
            ctx.addIssue({ code: "custom", message: "Invalid JSON" });
          }
        });
        break;

      default:
        schema = z.any();
    }

    // Apply default value
    if (field.defaultValue !== undefined) {
      schema = schema.default(field.defaultValue);
    }

    // Make optional if not required
    // Optional fields may be blank: undefined or null both mean "omit".
    if (!field.required) {
      schema = schema.nullable().optional();
    }

    shape[field.name] = schema;
  }

  return z.object(shape);
}
