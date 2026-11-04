/**
 * Agent EXECUTION types — what one selected agent needs from a person before
 * it runs, and nothing else.
 *
 * 🚨 There are no LIST types here. The agent list, its row shape, its tabs,
 * sorts, filters and counts all live in `@ai-matrx/agents/catalog` — THE ONE
 * AGENT PICKER (rulings D1/D4). `AgentInfo`, `AgentsResponse`, `AgentSource`
 * and `ActiveAgent` were this repo's private copy of that row and are DELETED;
 * re-adding one is re-adding the drift the package exists to end.
 */

import type { AgentVariableDefinition, VariableComponentSpec } from "@ai-matrx/api/agents";

// ---- Variable system ----

export type VariableComponentType =
  | "textarea"
  | "text"
  | "select"
  | "radio"
  | "checkbox"
  | "toggle"
  | "number";

/** The platform's one variable declaration (generated from the server contract), not a local copy. */
export type PromptVariable = AgentVariableDefinition;
export type VariableCustomComponent = VariableComponentSpec;

/** The declared default as the text a field shows: a string stays itself, null/absent is empty, anything else is its JSON. */
export function variableDefaultText(variable: PromptVariable): string {
  const d = variable.defaultValue;
  if (typeof d === "string") return d;
  if (d === null || d === undefined) return "";
  return JSON.stringify(d);
}

// ---- One agent's execution settings ----

export interface AgentSettings {
  model_id?: string | null;
  temperature?: number | null;
  max_tokens?: number | null;
  stream?: boolean;
  tools?: string[];
}
