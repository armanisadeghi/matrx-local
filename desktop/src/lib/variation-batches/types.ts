export type VariationItemStatus = "pending" | "generating" | "done" | "failed";

export interface VariationItem {
  id: string;
  prompt: string;
  negativePrompt: string;
  status: VariationItemStatus;
  error: string;
  updatedAt: number;
}

export interface VariationBatch {
  id: string;
  name: string;
  sourcePromptId: string | null;
  templatePrompt: string;
  templateNegative: string;
  /** Maps {{token}} name → saved list id (persisted with the batch). */
  variableListByName: Record<string, string>;
  items: VariationItem[];
  createdAt: number;
  updatedAt: number;
}

function normalizeVariableListByName(value: unknown): Record<string, string> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return {};
  }
  const out: Record<string, string> = {};
  for (const [key, listId] of Object.entries(value)) {
    if (typeof key === "string" && typeof listId === "string") {
      out[key] = listId;
    }
  }
  return out;
}

export function isVariationItem(value: unknown): value is VariationItem {
  if (typeof value !== "object" || value === null) return false;
  const row = value as Partial<VariationItem>;
  return (
    typeof row.id === "string" &&
    typeof row.prompt === "string" &&
    typeof row.negativePrompt === "string" &&
    typeof row.status === "string" &&
    typeof row.error === "string" &&
    typeof row.updatedAt === "number"
  );
}

export function isVariationBatch(value: unknown): value is VariationBatch {
  if (typeof value !== "object" || value === null) return false;
  const row = value as Partial<VariationBatch>;
  return (
    typeof row.id === "string" &&
    typeof row.name === "string" &&
    (row.sourcePromptId === null || typeof row.sourcePromptId === "string") &&
    typeof row.templatePrompt === "string" &&
    typeof row.templateNegative === "string" &&
    Array.isArray(row.items) &&
    typeof row.createdAt === "number" &&
    typeof row.updatedAt === "number"
  );
}

export function sanitizeVariationBatches(raw: unknown[]): VariationBatch[] {
  return raw
    .filter(isVariationBatch)
    .map((row) => ({
      ...row,
      name: row.name.trim() || "Untitled batch",
      variableListByName: normalizeVariableListByName(
        (row as VariationBatch).variableListByName,
      ),
      items: row.items.filter(isVariationItem),
    }))
    .sort((a, b) => b.updatedAt - a.updatedAt);
}

export function makeBatchId(): string {
  return crypto.randomUUID();
}

export function makeVariationItemId(): string {
  return crypto.randomUUID();
}

export function emptyVariationBatch(name = "New batch"): VariationBatch {
  const now = Date.now();
  return {
    id: makeBatchId(),
    name,
    sourcePromptId: null,
    templatePrompt: "",
    templateNegative: "",
    variableListByName: {},
    items: [],
    createdAt: now,
    updatedAt: now,
  };
}

export function emptyVariationItem(
  prompt = "",
  negativePrompt = "",
): VariationItem {
  return {
    id: makeVariationItemId(),
    prompt,
    negativePrompt,
    status: "pending",
    error: "",
    updatedAt: Date.now(),
  };
}
