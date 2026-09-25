/**
 * System Prompts Library
 *
 * Persists user-defined system prompts to localStorage; the built-in prompts
 * are Mandates (see builtinPrompts()). Import { systemPrompts } anywhere to
 * read/write prompts.
 * The PromptPicker component (components/PromptPicker.tsx) provides the UI.
 */

import {
  LOCAL_MODEL_MANDATE_KEYS,
  resolveLocalMandate,
  type LocalModelMandateKey,
} from "@/lib/local-mandates";

export interface SystemPrompt {
  id: string;
  name: string;
  content: string;
  category: string;
  createdAt: number;
  updatedAt: number;
  isPinned: boolean;
}

export interface CreateSystemPromptInput {
  name: string;
  content: string;
  category?: string;
}

const STORAGE_KEY = "matrx-system-prompts";

export type BuiltinPrompt = Omit<
  SystemPrompt,
  "createdAt" | "updatedAt" | "isPinned"
>;

// ── Built-in prompts: each one a Mandate ─────────────────────────────────────
//
// The built-in library is the `local.chat_persona_*` Mandates: the prompt text
// is the resolved Holder's system message, so a rebind in the mandate console
// reaches every desktop. The engine keeps the last answer for offline use
// (GET /local-mandates/{key}); there is no prompt copy in this repo. Until a
// builtin resolves it is absent from the list — never a stand-in text.

const BUILTIN_PROMPT_MANDATES: ReadonlyArray<{
  id: string;
  name: string;
  category: string;
  mandateKey: LocalModelMandateKey;
}> = [
  { id: "builtin-assistant", name: "Helpful Assistant", category: "General", mandateKey: LOCAL_MODEL_MANDATE_KEYS.chatPersonaHelpful },
  { id: "builtin-transcript-polish", name: "Transcript Polish", category: "Voice", mandateKey: LOCAL_MODEL_MANDATE_KEYS.chatPersonaTranscriptPolish },
  { id: "builtin-summarize", name: "Summarize", category: "Writing", mandateKey: LOCAL_MODEL_MANDATE_KEYS.chatPersonaSummarize },
  { id: "builtin-explain", name: "Explain Simply", category: "Writing", mandateKey: LOCAL_MODEL_MANDATE_KEYS.chatPersonaExplainSimply },
  { id: "builtin-code-review", name: "Code Review", category: "Development", mandateKey: LOCAL_MODEL_MANDATE_KEYS.chatPersonaCodeReview },
  { id: "builtin-brainstorm", name: "Brainstorm", category: "Creative", mandateKey: LOCAL_MODEL_MANDATE_KEYS.chatPersonaBrainstorm },
  { id: "builtin-voice-assistant", name: "Voice Assistant", category: "Voice", mandateKey: LOCAL_MODEL_MANDATE_KEYS.chatPersonaSpokenReplies },
];

let resolvedBuiltins: BuiltinPrompt[] = [];
let refreshInFlight: Promise<void> | null = null;

/** The built-in prompts resolved so far (each from its Mandate's Holder). */
export function builtinPrompts(): BuiltinPrompt[] {
  return resolvedBuiltins;
}

/**
 * Resolve every built-in prompt's Mandate (engine-cached, so it works
 * offline once resolved). Success dispatches "matrx-prompts-changed" so open
 * UIs re-read; a builtin that cannot be resolved is left out and logged with
 * its reason.
 */
export async function refreshBuiltinPrompts(): Promise<void> {
  if (refreshInFlight) return refreshInFlight;
  refreshInFlight = (async () => {
    try {
      const settled = await Promise.allSettled(
        BUILTIN_PROMPT_MANDATES.map(async (b) => {
          const holder = await resolveLocalMandate(b.mandateKey);
          const content = holder.messages.find((m) => m.role === "system")?.content ?? "";
          if (!content.trim()) {
            throw new Error(`${b.mandateKey}'s Holder has no system instructions`);
          }
          return { id: b.id, name: b.name, category: b.category, content };
        }),
      );
      const prompts: BuiltinPrompt[] = [];
      for (const r of settled) {
        if (r.status === "fulfilled") prompts.push(r.value);
        else console.warn("[system-prompts] built-in prompt unavailable:", r.reason);
      }
      const changed = JSON.stringify(prompts) !== JSON.stringify(resolvedBuiltins);
      resolvedBuiltins = prompts;
      if (changed) {
        window.dispatchEvent(new CustomEvent("matrx-prompts-changed"));
      }
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

// ── Storage helpers ──────────────────────────────────────────────────────────

function loadAll(): SystemPrompt[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as SystemPrompt[]) : [];
  } catch {
    return [];
  }
}

function saveAll(prompts: SystemPrompt[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(prompts));
    // Notify same-tab listeners (storage event only fires for cross-tab changes)
    window.dispatchEvent(new CustomEvent("matrx-prompts-changed"));
  } catch {
    // storage full — ignore
  }
}

// ── Public API ────────────────────────────────────────────────────────────────

export const systemPrompts = {
  /** All user-created prompts from localStorage. */
  list(): SystemPrompt[] {
    return loadAll();
  },

  /** All built-ins + user prompts, built-ins first. */
  listAll(): Array<
    SystemPrompt | Omit<SystemPrompt, "createdAt" | "updatedAt" | "isPinned">
  > {
    return [...builtinPrompts(), ...loadAll()];
  },

  get(id: string): SystemPrompt | undefined {
    return loadAll().find((p) => p.id === id);
  },

  getBuiltin(id: string) {
    return builtinPrompts().find((p) => p.id === id);
  },

  /** Get a prompt by id from either user or builtin list. */
  resolve(id: string): string | undefined {
    const builtin = builtinPrompts().find((p) => p.id === id);
    if (builtin) return builtin.content;
    return loadAll().find((p) => p.id === id)?.content;
  },

  create(input: CreateSystemPromptInput): SystemPrompt {
    const now = Date.now();
    const prompt: SystemPrompt = {
      id: crypto.randomUUID(),
      name: input.name.trim(),
      content: input.content.trim(),
      category: (input.category ?? "General").trim(),
      createdAt: now,
      updatedAt: now,
      isPinned: false,
    };
    const all = loadAll();
    saveAll([...all, prompt]);
    return prompt;
  },

  update(
    id: string,
    changes: Partial<
      Pick<SystemPrompt, "name" | "content" | "category" | "isPinned">
    >,
  ): boolean {
    const all = loadAll();
    const idx = all.findIndex((p) => p.id === id);
    if (idx === -1) return false;
    const current = all[idx];
    if (!current) return false;
    all[idx] = { ...current, ...changes, updatedAt: Date.now() };
    saveAll(all);
    return true;
  },

  delete(id: string): boolean {
    const all = loadAll();
    const next = all.filter((p) => p.id !== id);
    if (next.length === all.length) return false;
    saveAll(next);
    return true;
  },

  /** Fork a builtin into user's own list. */
  forkBuiltin(builtinId: string): SystemPrompt | null {
    const builtin = builtinPrompts().find((p) => p.id === builtinId);
    if (!builtin) return null;
    return systemPrompts.create({
      name: `${builtin.name} (copy)`,
      content: builtin.content,
      category: builtin.category,
    });
  },

  /** Duplicate a user prompt, appending " (copy)" to the name. */
  duplicate(id: string): SystemPrompt | null {
    const all = loadAll();
    const source = all.find((p) => p.id === id);
    if (!source) return null;
    return systemPrompts.create({
      name: `${source.name} (copy)`,
      content: source.content,
      category: source.category,
    });
  },

  categories(): string[] {
    const cats = new Set<string>();
    builtinPrompts().forEach((p) => cats.add(p.category));
    loadAll().forEach((p) => cats.add(p.category));
    return Array.from(cats).sort();
  },
};
