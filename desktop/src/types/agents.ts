/**
 * Agent / Prompt types — mirrors the Supabase schema for prompts and
 * prompt_builtins tables, plus the variable system used in GuidedVariableInputs.
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

// ---- Agent shape returned by /chat/agents ----

export type AgentSource = "builtin" | "user" | "shared";

export interface AgentSettings {
  model_id?: string | null;
  temperature?: number | null;
  max_tokens?: number | null;
  stream?: boolean;
  tools?: string[];
}

export interface AgentInfo {
  id: string;
  name: string;
  description: string;
  source: AgentSource;
  variable_defaults: PromptVariable[];
  settings: AgentSettings;
  category?: string | null;
  tags?: string[] | null;
  is_favorite?: boolean;
  is_owner?: boolean;
  access_level?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface AgentsResponse {
  builtins: AgentInfo[];
  user: AgentInfo[];
  shared: AgentInfo[];
  source: "sqlite" | "database" | "postgrest" | "fallback" | "error";
  syncing?: boolean;
  totals: {
    builtins: number;
    user: number;
    shared: number;
    total: number;
  };
}

// ---- Active agent state used by the chat ----

export interface ActiveAgent {
  id: string;
  name: string;
  description: string;
  source: AgentSource;
  variable_defaults: PromptVariable[];
  settings: AgentSettings;
  category?: string | null;
  tags?: string[] | null;
  is_favorite?: boolean;
}
