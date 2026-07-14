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
}

interface AgentExecutionMinimalRow {
  id: string;
  variable_definitions: unknown;
  context_slots: unknown;
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
  if (!raw || typeof raw.name !== "string" || raw.name.trim().length === 0) {
    return null;
  }

  const variable: PromptVariable = {
    name: raw.name,
  };

  if ("defaultValue" in raw) variable.defaultValue = asString(raw.defaultValue);
  if (typeof raw.helpText === "string") variable.helpText = raw.helpText;
  if (typeof raw.required === "boolean") variable.required = raw.required;

  const customComponent = normalizeCustomComponent(raw.customComponent);
  if (customComponent) variable.customComponent = customComponent;

  return variable;
}

function normalizeVariableList(value: unknown): PromptVariable[] {
  if (!Array.isArray(value)) return [];
  return value
    .map(normalizeVariableDefinition)
    .filter((variable): variable is PromptVariable => variable !== null);
}

function sourceFromRow(row: AgentListRow): AgentSource {
  if (row.agent_type === "builtin" || row.access_level === "system") return "builtin";
  if (row.is_owner) return "user";
  return "shared";
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
  };
}

export async function fetchCloudAgents(): Promise<AgentInfo[]> {
  const { data, error } = await supabase.rpc("agx_get_list_full");
  if (error) throw new Error(error.message);

  return ((data ?? []) as AgentListRow[])
    .filter((row) => row.is_active !== false && row.is_archived !== true)
    .map(agentFromRow)
    .sort((a, b) => a.name.localeCompare(b.name));
}

export async function fetchCloudAgentExecutionMinimal(
  agentId: string,
): Promise<{ variables: PromptVariable[]; contextSlots: unknown[] }> {
  const { data, error } = await supabase.rpc("agx_get_execution_minimal", {
    p_agent_id: agentId,
  });
  if (error) throw new Error(error.message);

  const row = Array.isArray(data)
    ? (data[0] as AgentExecutionMinimalRow | undefined)
    : (data as AgentExecutionMinimalRow | null);

  if (!row) return { variables: [], contextSlots: [] };

  const contextSlots = Array.isArray(row.context_slots) ? row.context_slots : [];
  return {
    variables: normalizeVariableList(row.variable_definitions),
    contextSlots,
  };
}
