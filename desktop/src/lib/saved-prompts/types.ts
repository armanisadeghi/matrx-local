export interface SavedPrompt {
  id: string;
  name: string;
  prompt: string;
  negativePrompt: string;
  createdAt: number;
  updatedAt: number;
}

export function isSavedPrompt(value: unknown): value is SavedPrompt {
  if (typeof value !== "object" || value === null) return false;
  const row = value as Partial<SavedPrompt>;
  return (
    typeof row.id === "string" &&
    typeof row.name === "string" &&
    typeof row.prompt === "string" &&
    typeof row.negativePrompt === "string" &&
    typeof row.createdAt === "number" &&
    typeof row.updatedAt === "number"
  );
}

export function sanitizeSavedPrompts(raw: unknown[]): SavedPrompt[] {
  return raw
    .filter(isSavedPrompt)
    .map((row) => ({
      ...row,
      name: row.name.trim() || "Untitled",
      prompt: row.prompt,
      negativePrompt: row.negativePrompt.trim(),
    }))
    .sort((a, b) => b.updatedAt - a.updatedAt);
}

export function makePromptId(): string {
  return crypto.randomUUID();
}

export function emptySavedPrompt(name = "New prompt"): SavedPrompt {
  const now = Date.now();
  return {
    id: makePromptId(),
    name,
    prompt: "",
    negativePrompt: "",
    createdAt: now,
    updatedAt: now,
  };
}
