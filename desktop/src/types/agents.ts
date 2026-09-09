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

// ---- Variable system ----

export type VariableComponentType =
  | "textarea"
  | "text"
  | "select"
  | "radio"
  | "checkbox"
  | "toggle"
  | "number";

export interface VariableCustomComponent {
  type: VariableComponentType;
  options?: string[];
  allowOther?: boolean;
  toggleValues?: [string, string];
  min?: number;
  max?: number;
  step?: number;
}

export interface PromptVariable {
  name: string;
  defaultValue?: string;
  helpText?: string;
  required?: boolean;
  customComponent?: VariableCustomComponent;
}

// ---- One agent's execution settings ----

export interface AgentSettings {
  model_id?: string | null;
  temperature?: number | null;
  max_tokens?: number | null;
  stream?: boolean;
  tools?: string[];
}
