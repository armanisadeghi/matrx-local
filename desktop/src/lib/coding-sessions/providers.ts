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
  ClaudeConversation,
  ClaudeOverview,
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
}

/** Which providers the overview payload lists sessions for. */
export function listedProviders(
  overview: ClaudeOverview | null,
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
  row: ClaudeConversation,
  overview: ClaudeOverview | null,
): CodingSessionProvider | null {
  if (row.provider) return row.provider;
  const listed = listedProviders(overview);
  // One listed provider and no per-row field: every row is that provider.
  return listed.length === 1 ? listed[0]! : null;
}

export function providerChips(
  overview: ClaudeOverview | null,
  readiness: CodingSessionProviderReadinessStatus | null,
): ProviderChip[] {
  const listed = new Set(listedProviders(overview));
  const counts = new Map<CodingSessionProvider, number>();
  for (const row of overview?.conversations ?? []) {
    const provider = rowProvider(row, overview);
    if (!provider) continue;
    counts.set(provider, (counts.get(provider) ?? 0) + 1);
  }
  const known = new Set<CodingSessionProvider>([
    ...(Object.keys(readiness?.providers ?? {}) as CodingSessionProvider[]),
    ...listed,
    ...counts.keys(),
  ]);
  return [...known]
    .map((provider) => ({
      provider,
      label: providerLabel(provider),
      count: counts.get(provider) ?? 0,
      listed: listed.has(provider),
      installed: readiness?.providers?.[provider]?.product.installed ?? null,
    }))
    .sort((left, right) =>
      right.count - left.count || left.label.localeCompare(right.label),
    );
}

export function filterRowsByProvider(
  rows: ClaudeConversation[],
  overview: ClaudeOverview | null,
  provider: CodingSessionProvider | null,
): ClaudeConversation[] {
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
