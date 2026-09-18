/**
 * The provider filter, derived — never a hard-coded "Claude Code".
 *
 * "The coding session tab got almost entirely taken over by Claude code
 * stuff" (Arman, 2026-09-14). The screen was built Claude-only at inception.
 * The chips below come from what the engine reports: every provider it knows
 * about (readiness) is a chip, and the ones it can actually LIST sessions for
 * say so. The day the engine lists Codex or Cursor transcripts, the filter
 * grows with no edit here.
 */

import type {
  CodingSessionProviderBlock,
  CodingSessionRow,
  CodingSessionsOverview,
  CodingSessionProvider,
  CodingSessionProviderReadinessStatus,
} from "@/lib/api";

export const PROVIDER_LABELS: Record<CodingSessionProvider, string> = {
  claude_code: "Claude Code",
  codex: "Codex",
  cursor: "Cursor",
  vscode: "VS Code",
};

export function providerLabel(provider: CodingSessionProvider | string): string {
  return PROVIDER_LABELS[provider as CodingSessionProvider] ?? provider;
}

export interface ProviderChip {
  provider: CodingSessionProvider;
  label: string;
  /** Rows this payload holds for the provider. */
  count: number;
  /** True when the engine lists sessions for it at all. */
  listed: boolean;
  installed: boolean | null;
  /**
   * THE ONE SENTENCE saying what this provider cannot show, in the engine's
   * own words (`CodingSessionProviderBlock.note`). Null = the engine said
   * nothing, and `providerNote` falls back to the unlisted reason rather than
   * to silence.
   */
  note: string | null;
  /** False = this Mac keeps no local record; its rows came from AI Matrx. */
  listsLocally: boolean;
  /** False = the provider has NO pin concept; the control is absent, not dead. */
  supportsPins: boolean;
  /** False = no command and no runtime reopens one of its chats. */
  supportsResume: boolean;
}

/** ONE provider's own half of the payload, or null when the engine sent none. */
export function providerBlock(
  overview: CodingSessionsOverview | null,
  provider: CodingSessionProvider,
): CodingSessionProviderBlock | null {
  return overview?.providers?.find((block) => block.provider === provider) ?? null;
}

/** Which providers the overview payload lists sessions for. */
export function listedProviders(
  overview: CodingSessionsOverview | null,
): CodingSessionProvider[] {
  if (!overview) return [];
  if (overview.listed_providers?.length) return overview.listed_providers;
  const fromRows = new Set<CodingSessionProvider>();
  for (const row of overview.conversations) {
    if (row.provider) fromRows.add(row.provider);
  }
  return [...fromRows];
}

export function rowProvider(
  row: CodingSessionRow,
  overview: CodingSessionsOverview | null,
): CodingSessionProvider | null {
  if (row.provider) return row.provider;
  const listed = listedProviders(overview);
  // One listed provider and no per-row field: every row is that provider.
  return listed.length === 1 ? listed[0]! : null;
}

export function providerChips(
  overview: CodingSessionsOverview | null,
  readiness: CodingSessionProviderReadinessStatus | null,
): ProviderChip[] {
  const listed = new Set(listedProviders(overview));
  const blocks = new Map<CodingSessionProvider, CodingSessionProviderBlock>(
    (overview?.providers ?? []).map((block) => [block.provider, block]),
  );
  const counts = new Map<CodingSessionProvider, number>();
  for (const row of overview?.conversations ?? []) {
    const provider = rowProvider(row, overview);
    if (!provider) continue;
    counts.set(provider, (counts.get(provider) ?? 0) + 1);
  }
  const known = new Set<CodingSessionProvider>([
    ...(Object.keys(readiness?.providers ?? {}) as CodingSessionProvider[]),
    ...listed,
    ...blocks.keys(),
    ...counts.keys(),
  ]);
  // The engine's own screen order for every provider it sent a block for, so
  // the chips never reshuffle under a person's cursor between two reads just
  // because one provider's count overtook another's.
  const screenOrder = [...blocks.keys()];
  return [...known]
    .map((provider) => {
      const block = blocks.get(provider) ?? null;
      return {
        provider,
        label: providerLabel(provider),
        count: counts.get(provider) ?? 0,
        listed: listed.has(provider),
        installed: readiness?.providers?.[provider]?.product.installed ?? null,
        note: block?.note ?? null,
        // No block = an engine before schema 3, which listed Claude Code alone
        // and is the only provider that has ever had pins or a resume here.
        listsLocally: block?.lists_locally ?? listed.has(provider),
        supportsPins: block?.supports_pins ?? provider === "claude_code",
        supportsResume: block?.supports_resume ?? provider === "claude_code",
      };
    })
    .sort((left, right) => {
      const leftAt = screenOrder.indexOf(left.provider);
      const rightAt = screenOrder.indexOf(right.provider);
      if (leftAt !== -1 && rightAt !== -1) return leftAt - rightAt;
      if (leftAt !== -1) return -1;
      if (rightAt !== -1) return 1;
      return right.count - left.count || left.label.localeCompare(right.label);
    });
}

export function filterRowsByProvider(
  rows: CodingSessionRow[],
  overview: CodingSessionsOverview | null,
  provider: CodingSessionProvider | null,
): CodingSessionRow[] {
  if (!provider) return rows;
  return rows.filter((row) => rowProvider(row, overview) === provider);
}

/** What an empty provider tab says, so no chip is ever a dead end. */
export function unlistedProviderNote(chip: ProviderChip): string {
  if (chip.listed) return `No ${chip.label} sessions match this search or filter.`;
  return (
    `This Mac's engine does not list ${chip.label} sessions yet. ` +
    `${chip.label} still delivers through the same bridge — its queue and ` +
    `readiness are on the Settings & diagnostics tab.`
  );
}

/**
 * The sentence a provider's chip shows, and it always has one: the engine's
 * own reason when it sent one, the unlisted reason when it did not. A chip
 * with neither would be a dead end (law 4).
 */
export function providerNote(chip: ProviderChip): string {
  return chip.note ?? unlistedProviderNote(chip);
}

/**
 * Whether a ROW has pins at all — asked of the provider that wrote it, never
 * of the pin value. `pinned: null` means the provider has no pin concept,
 * which is not the same fact as "not pinned": the control is absent, not off.
 */
export function rowSupportsPins(
  row: CodingSessionRow,
  overview: CodingSessionsOverview | null,
): boolean {
  const provider = rowProvider(row, overview);
  const block = provider ? providerBlock(overview, provider) : null;
  if (block) return block.supports_pins;
  if (row.pinned === null) return false;
  if (row.pinned !== undefined) return true;
  return provider === "claude_code";
}

/**
 * What stands where a size would be when the provider has none. A Cursor chat
 * is rows in a shared database, so its size is MISSING, and a missing size is
 * never rendered as "0 B" — a zero is a claim.
 */
export function noSizeSentence(label: string): string {
  return `${label} reports no size for a session, so there is none to show here.`;
}
