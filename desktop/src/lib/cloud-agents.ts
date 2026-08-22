import {
  DEFAULT_CHAT_MANDATE_REF,
  isMandateAgentRef,
} from "@/lib/agents/mandates";
import supabase from "@/lib/supabase";
import type {
  AgentInfo,
  AgentSettings,
  AgentSource,
  PromptVariable,
  VariableComponentType,
  VariableCustomComponent,
} from "@/types/agents";

interface AgentListRow {
  id: string;
  name: string | null;
  description: string | null;
  category: string | null;
  tags: string[] | null;
  agent_type: string | null;
  model_id: string | null;
  is_active: boolean | null;
  is_archived: boolean | null;
  is_favorite: boolean | null;
  is_owner: boolean | null;
  access_level: string | null;
  created_at: string | null;
  updated_at: string | null;
}

interface AgentExecutionFullRow {
  id: string;
  variable_definitions: unknown;
  context_slots: unknown;
  model_id: string | null;
  settings: unknown;
  tools: string[] | null;
  custom_tools: unknown;
  ui_gates: unknown;
}

const SUPPORTED_COMPONENT_TYPES = new Set<VariableComponentType>([
  "textarea",
  "text",
  "select",
  "radio",
  "checkbox",
  "toggle",
  "number",
]);

function asString(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

function stringArray(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const next = value.filter((item): item is string => typeof item === "string");
  return next.length > 0 ? next : undefined;
}

function readRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function readString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim().length > 0
    ? value
    : undefined;
}

function readNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function normalizeComponentType(value: unknown): VariableComponentType {
  if (typeof value === "string" && SUPPORTED_COMPONENT_TYPES.has(value as VariableComponentType)) {
    return value as VariableComponentType;
  }
  if (value === "buttons") return "select";
  return "textarea";
}

function normalizeCustomComponent(value: unknown): VariableCustomComponent | undefined {
  const raw = readRecord(value);
  if (!raw) return undefined;

  const component: VariableCustomComponent = {
    type: normalizeComponentType(raw.type),
  };

  const options = stringArray(raw.options);
  if (options) component.options = options;
  if (typeof raw.allowOther === "boolean") component.allowOther = raw.allowOther;
  if (
    Array.isArray(raw.toggleValues) &&
    typeof raw.toggleValues[0] === "string" &&
    typeof raw.toggleValues[1] === "string"
  ) {
    component.toggleValues = [raw.toggleValues[0], raw.toggleValues[1]];
  }
  if (typeof raw.min === "number") component.min = raw.min;
  if (typeof raw.max === "number") component.max = raw.max;
  if (typeof raw.step === "number") component.step = raw.step;

  return component;
}

export function normalizeVariableDefinition(value: unknown): PromptVariable | null {
  const raw = readRecord(value);
  const name = readString(raw?.name) ?? readString(raw?.key);
  if (!raw || !name) {
    return null;
  }

  const variable: PromptVariable = {
    name,
  };

  if ("defaultValue" in raw) variable.defaultValue = asString(raw.defaultValue);
  else if ("default_value" in raw) variable.defaultValue = asString(raw.default_value);
  else if ("default" in raw) variable.defaultValue = asString(raw.default);

  if (typeof raw.helpText === "string") variable.helpText = raw.helpText;
  else if (typeof raw.help_text === "string") variable.helpText = raw.help_text;
  else if (typeof raw.description === "string") variable.helpText = raw.description;

  if (typeof raw.required === "boolean") variable.required = raw.required;

  const customComponent = normalizeCustomComponent(
    raw.customComponent ?? raw.custom_component ?? raw.component,
  );
  if (customComponent) variable.customComponent = customComponent;

  return variable;
}

function normalizeVariableList(value: unknown): PromptVariable[] {
  const values = Array.isArray(value)
    ? value
    : Object.entries(readRecord(value) ?? {}).map(([name, raw]) => {
        const record = readRecord(raw);
        return record && !("name" in record) ? { ...record, name } : raw;
      });

  return values
    .map(normalizeVariableDefinition)
    .filter((variable): variable is PromptVariable => variable !== null);
}

function sourceFromRow(row: AgentListRow): AgentSource {
  if (row.agent_type === "builtin" || row.access_level === "system") return "builtin";
  if (row.is_owner) return "user";
  return "shared";
}

function settingsFromUnknown(value: unknown): AgentSettings {
  const raw = readRecord(value);
  if (!raw) return {};

  const settings: AgentSettings = {};
  const modelId = readString(raw.model_id ?? raw.modelId ?? raw.ai_model_id);
  if (modelId) settings.model_id = modelId;

  const temperature = readNumber(raw.temperature);
  if (temperature !== undefined) settings.temperature = temperature;

  const maxTokens = readNumber(raw.max_tokens ?? raw.maxTokens);
  if (maxTokens !== undefined) settings.max_tokens = maxTokens;

  if (typeof raw.stream === "boolean") settings.stream = raw.stream;

  const tools = stringArray(raw.tools);
  if (tools) settings.tools = tools;

  return settings;
}

function settingsFromRow(row: AgentListRow): AgentSettings {
  return row.model_id ? { model_id: row.model_id } : {};
}

function agentFromRow(row: AgentListRow): AgentInfo {
  return {
    id: row.id,
    name: row.name?.trim() || "Untitled agent",
    description: row.description ?? "",
    source: sourceFromRow(row),
    variable_defaults: [],
    settings: settingsFromRow(row),
    category: row.category,
    tags: row.tags ?? [],
    is_favorite: Boolean(row.is_favorite),
    is_owner: Boolean(row.is_owner),
    access_level: row.access_level,
    created_at: row.created_at,
    updated_at: row.updated_at,
  };
}

/**
 * Synthetic UI entry for the canonical Cloud Chat Mandate (`local.cloud_chat`).
 * Always first in the list so a user with no visible agents has a working
 * choice. The synthetic id is UI state only; execution sends the mandate key
 * to aidream and the server resolves the agent for the current principal at
 * run time — an admin repointing the Mandate changes desktop with no deploy.
 */
export const DEFAULT_CHAT_AGENT: AgentInfo = {
  id: DEFAULT_CHAT_MANDATE_REF,
  name: "Matrx Desktop Agent",
  description: "Default Matrx agent for Cloud Chat, resolved by the platform when you send.",
  source: "builtin",
  variable_defaults: [],
  settings: {},
  category: null,
  tags: [],
  is_favorite: false,
  is_owner: false,
  access_level: "system",
  created_at: null,
  updated_at: null,
};

export async function fetchCloudAgents(): Promise<AgentInfo[]> {
  const { data, error } = await supabase.rpc("agx_get_list_full");
  if (error) throw new Error(error.message);

  const visible = ((data ?? []) as AgentListRow[])
    .filter((row) => row.is_active !== false && row.is_archived !== true)
    .map(agentFromRow);
  return [DEFAULT_CHAT_AGENT, ...visible];
}

export async function fetchCloudAgentExecutionFull(
  agentId: string,
): Promise<{
  variables: PromptVariable[];
  contextSlots: unknown[];
  modelId: string | null;
  settings: AgentSettings;
  tools: string[];
  customTools: unknown;
  uiGates: unknown;
}> {
  // A Mandate-backed choice has no client-readable definition: the server
  // resolves the agent (and its variables) at run time.
  if (isMandateAgentRef(agentId)) {
    return {
      variables: [],
      contextSlots: [],
      modelId: null,
      settings: {},
      tools: [],
      customTools: null,
      uiGates: null,
    };
  }
  const { data, error } = await supabase.rpc("agx_get_execution_full", {
    p_agent_id: agentId,
  });
  if (error) throw new Error(error.message);

  const row = Array.isArray(data)
    ? (data[0] as AgentExecutionFullRow | undefined)
    : (data as AgentExecutionFullRow | null);

  if (!row) {
    return {
      variables: [],
      contextSlots: [],
      modelId: null,
      settings: {},
      tools: [],
      customTools: null,
      uiGates: null,
    };
  }

  const contextSlots = Array.isArray(row.context_slots) ? row.context_slots : [];
  const settings = settingsFromUnknown(row.settings);
  const modelId = row.model_id ?? settings.model_id ?? null;

  return {
    variables: normalizeVariableList(row.variable_definitions),
    contextSlots,
    modelId,
    settings: {
      ...settings,
      ...(modelId ? { model_id: modelId } : {}),
      ...(row.tools?.length ? { tools: row.tools } : {}),
    },
    tools: row.tools ?? [],
    customTools: row.custom_tools,
    uiGates: row.ui_gates,
  };
}

export const fetchCloudAgentExecutionMinimal = fetchCloudAgentExecutionFull;
