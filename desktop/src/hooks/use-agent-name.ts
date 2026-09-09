/**
 * The NAME of the selected agent, asked of the shared catalog — never stored.
 *
 * P0c (design D3): the desktop used to print a hardcoded "Matrx Desktop Agent"
 * for the `local.cloud_chat` placeholder while the mandate actually resolved to
 * General Chat — a label lying on screen with nothing able to notice. There is
 * no agent name in this repo any more. A `mandate:<key>` selection is named by
 * the catalog's live resolution of that mandate's Holder; anything else is
 * named by its catalog row.
 *
 * Returns `null` while the name is genuinely unknown, so a caller renders
 * nothing rather than inventing a placeholder.
 */

import {
  useAgentCatalogRows,
  useAgentCatalogState,
} from "@ai-matrx/agents/catalog/react";
import type { AgentCatalogState } from "@ai-matrx/agents/catalog";

import { mandateKeyFromAgentRef } from "@/lib/mandates";

const selectDefaultRows = (state: AgentCatalogState) => state.defaultRows;

export function useAgentName(agentId: string | null): string | null {
  const rows = useAgentCatalogRows();
  const defaultRows = useAgentCatalogState(selectDefaultRows);
  if (!agentId) return null;

  const mandateKey = mandateKeyFromAgentRef(agentId);
  if (mandateKey) {
    return defaultRows[mandateKey]?.row?.name ?? null;
  }
  return rows.find((row) => row.id === agentId)?.name ?? null;
}
