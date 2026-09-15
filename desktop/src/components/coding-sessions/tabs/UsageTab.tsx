/**
 * Usage, per provider, inside the one coding-sessions feature.
 *
 * Codex is the only provider this Mac has a usage source for today, and the
 * tab says exactly that for the others rather than showing an empty chart or
 * hiding them. The provider row is the same facet the Sessions tab uses, so
 * the day a Claude Code or Cursor meter exists it becomes a tab entry with no
 * new screen.
 */

import { useState } from "react";

import { Button } from "@ai-matrx/design-system";
import { CodexUsagePanel } from "@/components/coding-sessions/CodexUsagePanel";
import { providerChips, providerLabel } from "@/lib/coding-sessions/providers";
import type { CodingSessionsSnapshot } from "@/lib/coding-sessions/overview-store";
import type { CodingSessionProvider } from "@/lib/api";

/** Providers with a real local usage source in this engine. */
const USAGE_SOURCES: Partial<Record<CodingSessionProvider, "codex">> = {
  codex: "codex",
};

export interface UsageTabProps {
  snapshot: CodingSessionsSnapshot;
}

export function UsageTab({ snapshot }: UsageTabProps) {
  const chips = providerChips(snapshot.overview, snapshot.readiness);
  const providers: CodingSessionProvider[] = chips.length
    ? chips.map((chip) => chip.provider)
    : (Object.keys(USAGE_SOURCES) as CodingSessionProvider[]);
  const withSource = providers.filter((provider) => USAGE_SOURCES[provider]);
  const [provider, setProvider] = useState<CodingSessionProvider>(
    withSource[0] ?? providers[0] ?? "codex",
  );

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2" data-testid="usage-provider-chips">
        {providers.map((item) => (
          <Button
            key={item}
            size="sm"
            variant={provider === item ? "secondary" : "ghost"}
            title={
              USAGE_SOURCES[item]
                ? `Usage measured on this Mac for ${providerLabel(item)}.`
                : `No usage source for ${providerLabel(item)} yet.`
            }
            onClick={() => setProvider(item)}
          >
            {providerLabel(item)}
          </Button>
        ))}
      </div>

      {USAGE_SOURCES[provider] === "codex" ? (
        <section data-testid="usage-panel-codex">
          <h2 className="mb-1 text-sm font-medium">{providerLabel(provider)} usage</h2>
          <CodexUsagePanel />
        </section>
      ) : (
        <p
          className="rounded-lg border bg-muted/30 p-4 text-sm text-muted-foreground"
          data-testid="usage-no-source"
        >
          No usage source for {providerLabel(provider)} yet. This Mac measures usage
          only where the app writes a local ledger we can read — Codex does today.
          Nothing is hidden here: there is no {providerLabel(provider)} number to show.
        </p>
      )}
    </div>
  );
}
