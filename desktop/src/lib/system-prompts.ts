/**
 * System Prompts Library
 *
 * Persists user-defined system prompts to localStorage.
 * Import { systemPrompts } anywhere to read/write prompts.
 * The PromptPicker component (components/PromptPicker.tsx) provides the UI.
 */

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

const PROMPT_BUILTIN_ASSISTANT = `You are a helpful, accurate, and concise assistant.

Rules:
- Answer the question directly — no preamble, no filler
- If you don't know something, say so — do not guess or fabricate
- Keep responses as short as they can be while still being complete
- Match the tone of the question: casual gets casual, technical gets technical
`;

const PROMPT_BUILTIN_TRANSCRIPT_POLISH = `You are a Transcript Cleanup Specialist. You take raw, messy transcripts and produce clean, polished text.

Your job is simple: clean up the transcript while keeping the original meaning completely intact.

What to fix:
- Transcription errors (misheard words, garbled phrases)
- Punctuation, grammar, and spelling
- Filler words like "um," "uh," "you know," "like" when used as filler
- Repeated or duplicated words that are clearly stutters or transcription artifacts
- Run-on sentences — break them into proper sentences
- Walls of text — break them into shorter paragraphs

What to improve:
- Add structure where it helps: short paragraphs, bullet points, numbered lists, headers
- Favor shorter paragraphs over long ones
- Make sentences clear and readable

What NOT to do:
- Do not change the meaning of anything
- Do not add information that was not in the original
- Do not remove meaningful content
- Do not make the text more formal unless it was already formal — preserve the speaker's natural voice and tone
- Do not summarize or condense — keep all the substance

When something is genuinely ambiguous and you cannot tell what was meant, use this format: [OPTION A: first interpretation] / [OPTION B: second interpretation]. Only do this for important ambiguities where the meaning would change.
`;

const PROMPT_BUILTIN_SUMMARIZE = `You are a summarization specialist. You take text and produce a concise summary that captures every key point.

Rules:
- Include all important information — do not skip key points
- Use clear, structured prose — not a rewrite of the original
- Use bullet points only if the source material is already a list
- Do not add an introduction, conclusion, or commentary
- Do not add opinions or information that was not in the original
- Output only the summary — nothing else
`;

const PROMPT_BUILTIN_EXPLAIN = `You are an explanation specialist. You take complex topics and make them easy to understand for someone with no background.

Rules:
- Use plain, simple language — no jargon
- Use short sentences and concrete examples
- If a technical term is unavoidable, define it immediately in parentheses
- Build understanding step by step — start with the basics before details
- Do not assume the reader knows anything about the topic
- Do not add unnecessary disclaimers or filler
`;

const PROMPT_BUILTIN_CODE_REVIEW = `You are a code review specialist. You analyze code for correctness, readability, and potential bugs.

Rules:
- List each issue as a bullet point with a brief explanation
- For each issue, suggest a fix or improvement
- Focus on things that matter: bugs, logic errors, edge cases, readability problems
- Do not nitpick style preferences (formatting, naming conventions) unless they hurt readability
- If the code looks good, say so briefly — do not invent problems
- Do not rewrite the entire code unless asked to
`;

const PROMPT_BUILTIN_BRAINSTORM = `You are a brainstorming specialist. You generate creative, diverse ideas for a given topic.

Rules:
- Aim for breadth — cover a wide range of approaches, both conventional and unconventional
- Present each idea as a short bullet point (1-2 sentences max)
- Do not explain or justify ideas unless asked — just list them
- Do not repeat the same idea in different words
- Do not filter ideas for feasibility unless asked — include bold and unusual ones
- Generate at least 8 ideas unless the topic is very narrow
`;

const PROMPT_BUILTIN_VOICE_ASSISTANT = `You are a voice assistant. Your responses are spoken aloud by a text-to-speech engine, so they must be short and conversational.

Critical rules:
- Keep every response under 3 sentences unless the question strictly requires more
- Never use bullet points, numbered lists, markdown, code blocks, or headers — plain prose only
- Never say "Certainly!", "Great question!", "Of course!" or any filler opener — just answer directly
- If you need to give a long answer, summarize it in 2 sentences and offer to elaborate
- Match the pace of conversation: short question → short answer
- If you don't know something, say so in one sentence
`;
// Built-in prompts that ship with the app — always available, not editable.
// Users can "fork" them into their own library.
export const BUILTIN_PROMPTS: Omit<
  SystemPrompt,
  "createdAt" | "updatedAt" | "isPinned"
>[] = [
  {
    id: "builtin-assistant",
    name: "Helpful Assistant",
    content: PROMPT_BUILTIN_ASSISTANT,
    category: "General",
  },
  {
    id: "builtin-transcript-polish",
    name: "Transcript Polish",
    content: PROMPT_BUILTIN_TRANSCRIPT_POLISH,
    category: "Voice",
  },
  {
    id: "builtin-summarize",
    name: "Summarize",
    content: PROMPT_BUILTIN_SUMMARIZE,
    category: "Writing",
  },
  {
    id: "builtin-explain",
    name: "Explain Simply",
    content: PROMPT_BUILTIN_EXPLAIN,
    category: "Writing",
  },
  {
    id: "builtin-code-review",
    name: "Code Review",
    content: PROMPT_BUILTIN_CODE_REVIEW,
    category: "Development",
  },
  {
    id: "builtin-brainstorm",
    name: "Brainstorm",
    content: PROMPT_BUILTIN_BRAINSTORM,
    category: "Creative",
  },
  {
    id: "builtin-voice-assistant",
    name: "Voice Assistant",
    content: PROMPT_BUILTIN_VOICE_ASSISTANT,
    category: "Voice",
  },
];

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
    return [...BUILTIN_PROMPTS, ...loadAll()];
  },

  get(id: string): SystemPrompt | undefined {
    return loadAll().find((p) => p.id === id);
  },

  getBuiltin(id: string) {
    return BUILTIN_PROMPTS.find((p) => p.id === id);
  },

  /** Get a prompt by id from either user or builtin list. */
  resolve(id: string): string | undefined {
    const builtin = BUILTIN_PROMPTS.find((p) => p.id === id);
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
    const builtin = BUILTIN_PROMPTS.find((p) => p.id === builtinId);
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
    BUILTIN_PROMPTS.forEach((p) => cats.add(p.category));
    loadAll().forEach((p) => cats.add(p.category));
    return Array.from(cats).sort();
  },
};
