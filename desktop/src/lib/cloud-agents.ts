/**
 * ONE AGENT'S EXECUTION DETAIL — never a list.
 *
 * The agent LIST lives in `@ai-matrx/agents/catalog` and nowhere else (ruling
 * D1/D4). What remains here is the read a variables form makes for the ONE
 * agent a person selected: `agx_get_execution_full(p_agent_id)`. It is not a
 * catalog column and never appears in a list row, online or off — the offline
 * twin is `GET /agents/catalog/{agent_id}/execution` (see
 * `lib/agent-execution.ts`).
 */

import { isMandateAgentRef } from "@/lib/mandates";
import supabase from "@/lib/supabase";
import type {
  AgentSettings,
  PromptVariable,
  VariableComponentType,
  VariableCustomComponent,
} from "@/types/agents";

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

export function normalizeVariableList(value: unknown): PromptVariable[] {
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

/** The empty payload — a Mandate ref, or an agent with no readable row. */
export const EMPTY_EXECUTION_PAYLOAD = {
  variables: [] as PromptVariable[],
  contextSlots: [] as unknown[],
  modelId: null as string | null,
  settings: {} as AgentSettings,
  tools: [] as string[],
  customTools: null as unknown,
  uiGates: null as unknown,
};

export type AgentExecutionPayloadShape = typeof EMPTY_EXECUTION_PAYLOAD;

/**
 * ONE mapper for `agx_get_execution_full`'s row, whichever lane delivered it.
 *
 * Ruling D4: offline is a data LOCATION, never a different structure — the
 * engine's `/agents/catalog/{id}/execution` door serves the RPC row verbatim,
 * so cloud and local normalize through exactly this function. A second mapper
 * would be a second shape.
 */
export function executionPayloadFromRow(
  row: unknown,
): AgentExecutionPayloadShape {
  const record = readRecord(row) as AgentExecutionFullRow | null;
  if (!record) return { ...EMPTY_EXECUTION_PAYLOAD };

  const contextSlots = Array.isArray(record.context_slots)
    ? record.context_slots
    : [];
  const settings = settingsFromUnknown(record.settings);
  const modelId = record.model_id ?? settings.model_id ?? null;

  return {
    variables: normalizeVariableList(record.variable_definitions),
    contextSlots,
    modelId,
    settings: {
      ...settings,
      ...(modelId ? { model_id: modelId } : {}),
      ...(record.tools?.length ? { tools: record.tools } : {}),
    },
    tools: record.tools ?? [],
    customTools: record.custom_tools,
    uiGates: record.ui_gates,
  };
}

export async function fetchCloudAgentExecutionFull(
  agentId: string,
): Promise<AgentExecutionPayloadShape> {
  // A Mandate-backed choice has no client-readable definition: the server
  // resolves the agent (and its variables) at run time.
  if (isMandateAgentRef(agentId)) return { ...EMPTY_EXECUTION_PAYLOAD };

  const { data, error } = await supabase.rpc("agx_get_execution_full", {
    p_agent_id: agentId,
  });
  if (error) throw new Error(error.message);

  const row = Array.isArray(data) ? data[0] : data;
  return executionPayloadFromRow(row);
}

export const fetchCloudAgentExecutionMinimal = fetchCloudAgentExecutionFull;
