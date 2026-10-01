import type {
  ToolCategory,
  ToolFieldSchema,
  ToolUISchema,
} from "@/types/tool-schema";

interface EngineProperty {
  type?: string | string[];
  description?: string;
  default?: unknown;
  enum?: unknown[];
  anyOf?: EngineProperty[];
  items?: EngineProperty;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
  minLength?: number;
  maxLength?: number;
}

interface EngineToolSchema {
  name: string;
  description: string;
  category?: string;
  input_schema?: {
    properties?: Record<string, EngineProperty>;
    required?: string[];
  };
}

export interface CategoryMeta {
  id: string;
  label: string;
  description: string;
  icon: string;
  color: string;
  panelType: string;
}

// ── Map backend category names → consolidated UI groups ────────────────────────
const categoryMapping: Record<string, string> = {
  "System Monitoring": "system-monitor",
  "Process Management": "system-monitor",
  System: "system-monitor",

  "Network Discovery": "network",
  "WiFi & Bluetooth": "network",

  // Web fetch, scraping, search & research get their own group
  Network: "web-search",

  "File Operations": "files",
  "File Watching": "files",
  "File Transfer": "files",
  Documents: "files",

  Audio: "media",
  "Media Processing": "media",

  "Browser Automation": "browser",

  Clipboard: "clipboard",
  Notifications: "clipboard",

  "Window Management": "automation",
  "Input Automation": "automation",

  Scheduler: "scheduler",

  Execution: "terminal",
  "OS Integration": "terminal",
  PowerShell: "powershell",
};

export function mapCategory(backendCategory: string): string {
  return categoryMapping[backendCategory] ?? "terminal";
}

export const toolCategories: CategoryMeta[] = [
  {
    id: "system-monitor",
    label: "System",
    description: "CPU, memory, disk, battery & processes",
    icon: "activity",
    color: "violet",
    panelType: "monitoring",
  },
  {
    id: "network",
    label: "Network",
    description: "Interfaces, ports, WiFi & Bluetooth",
    icon: "wifi",
    color: "sky",
    panelType: "network",
  },
  {
    id: "web-search",
    label: "Web & Search",
    description: "Fetch URLs, scrape pages, search & research",
    icon: "search",
    color: "cyan",
    panelType: "generic",
  },
  {
    id: "files",
    label: "Files",
    description: "Read, write, search, transfer & documents",
    icon: "folder-open",
    color: "teal",
    panelType: "files",
  },
  {
    id: "media",
    label: "Media",
    description: "Audio, images, OCR, PDF & archives",
    icon: "image",
    color: "rose",
    panelType: "media",
  },
  {
    id: "browser",
    label: "Browser",
    description: "Navigate, click, extract & screenshot",
    icon: "globe",
    color: "purple",
    panelType: "browser",
  },
  {
    id: "clipboard",
    label: "Clipboard",
    description: "Read/write clipboard & notifications",
    icon: "clipboard",
    color: "amber",
    panelType: "clipboard",
  },
  {
    id: "automation",
    label: "Automation",
    description: "Window management, keyboard & mouse",
    icon: "mouse-pointer",
    color: "indigo",
    panelType: "automation",
  },
  {
    id: "scheduler",
    label: "Scheduler",
    description: "Scheduled tasks, heartbeat & sleep",
    icon: "clock",
    color: "orange",
    panelType: "scheduler",
  },
  {
    id: "terminal",
    label: "Terminal",
    description: "Shell commands, scripts & OS integration",
    icon: "terminal",
    color: "zinc",
    panelType: "terminal",
  },
  {
    id: "powershell",
    label: "PowerShell",
    description: "Environment, registry, services & event log",
    icon: "square-terminal",
    color: "blue",
    panelType: "generic",
  },
];

export const categoryColorMap: Record<
  string,
  { bg: string; text: string; border: string; glow: string }
> = {
  violet: {
    bg: "bg-violet-500/15",
    text: "text-violet-400",
    border: "border-violet-500/30",
    glow: "shadow-violet-500/20",
  },
  blue: {
    bg: "bg-blue-500/15",
    text: "text-blue-400",
    border: "border-blue-500/30",
    glow: "shadow-blue-500/20",
  },
  sky: {
    bg: "bg-sky-500/15",
    text: "text-sky-400",
    border: "border-sky-500/30",
    glow: "shadow-sky-500/20",
  },
  indigo: {
    bg: "bg-indigo-500/15",
    text: "text-indigo-400",
    border: "border-indigo-500/30",
    glow: "shadow-indigo-500/20",
  },
  amber: {
    bg: "bg-amber-500/15",
    text: "text-amber-400",
    border: "border-amber-500/30",
    glow: "shadow-amber-500/20",
  },
  rose: {
    bg: "bg-rose-500/15",
    text: "text-rose-400",
    border: "border-rose-500/30",
    glow: "shadow-rose-500/20",
  },
  orange: {
    bg: "bg-orange-500/15",
    text: "text-orange-400",
    border: "border-orange-500/30",
    glow: "shadow-orange-500/20",
  },
  yellow: {
    bg: "bg-yellow-500/15",
    text: "text-yellow-400",
    border: "border-yellow-500/30",
    glow: "shadow-yellow-500/20",
  },
  cyan: {
    bg: "bg-cyan-500/15",
    text: "text-cyan-400",
    border: "border-cyan-500/30",
    glow: "shadow-cyan-500/20",
  },
  teal: {
    bg: "bg-teal-500/15",
    text: "text-teal-400",
    border: "border-teal-500/30",
    glow: "shadow-teal-500/20",
  },
  purple: {
    bg: "bg-purple-500/15",
    text: "text-purple-400",
    border: "border-purple-500/30",
    glow: "shadow-purple-500/20",
  },
  pink: {
    bg: "bg-pink-500/15",
    text: "text-pink-400",
    border: "border-pink-500/30",
    glow: "shadow-pink-500/20",
  },
  slate: {
    bg: "bg-slate-500/15",
    text: "text-slate-400",
    border: "border-slate-500/30",
    glow: "shadow-slate-500/20",
  },
  zinc: {
    bg: "bg-zinc-500/15",
    text: "text-zinc-400",
    border: "border-zinc-500/30",
    glow: "shadow-zinc-500/20",
  },
};

const toFieldType = (type?: string): ToolFieldSchema["type"] => {
  switch (type) {
    case "integer":
    case "number":
      return "number";
    case "boolean":
      return "boolean";
    case "array":
      return "tags";
    case "object":
      return "json";
    default:
      return "text";
  }
};

const toTitleCase = (value: string) =>
  value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

/**
 * Collapse `anyOf` / type lists to the one shape the form can render.
 * Arrays win (tags), then strings (a text box accepts every scalar the
 * server will parse), then the first non-null branch.
 */
type ResolvedProperty = Omit<EngineProperty, "type"> & { type?: string | undefined };

function resolveProperty(def: EngineProperty): ResolvedProperty {
  if (Array.isArray(def.type)) {
    const t = def.type.find((x) => x !== "null");
    return { ...def, type: t };
  }
  if (def.type || !def.anyOf) return def as ResolvedProperty;
  const branches = def.anyOf
    .map(resolveProperty)
    .filter((b) => b.type !== "null");
  const { anyOf: _drop, ...rest } = def;
  // A list OR a mapping (e.g. ExtractEntities.labels) — only a JSON box can
  // express both.
  if (branches.some((b) => b.type === "array") && branches.some((b) => b.type === "object")) {
    return { ...rest, type: "object" };
  }
  const pick: ResolvedProperty =
    branches.find((b) => b.type === "array") ??
    branches.find((b) => b.type === "string") ??
    branches[0] ??
    {};
  return { ...pick, ...rest, type: pick.type };
}

function toField(
  name: string,
  raw: EngineProperty,
  isRequired: boolean,
): ToolFieldSchema {
  const def = resolveProperty(raw);
  const enumValues =
    def.enum && def.enum.length > 0 && def.enum.every((v) => typeof v === "string")
      ? (def.enum as string[])
      : null;
  // Only treat *string* params as file paths — the name heuristic was
  // overriding ARRAY types (e.g. ArchiveCreate.source_paths), so the form
  // submitted a plain string the handler then iterated per character.
  const isPath =
    !enumValues &&
    name.toLowerCase().includes("path") &&
    (def.type === "string" || def.type === undefined);
  const type: ToolFieldSchema["type"] = enumValues
    ? "select"
    : isPath
      ? "file-path"
      : toFieldType(def.type);

  const field: ToolFieldSchema = {
    name,
    label: toTitleCase(name),
    type,
    ...(def.description !== undefined ? { description: def.description } : {}),
    // A null default means "omitted" — never a value the form must hold.
    ...(def.default !== undefined && def.default !== null
      ? { defaultValue: def.default }
      : {}),
    required: isRequired,
    ...(isPath ? { placeholder: "/path/to/file" } : {}),
  };

  if (enumValues) {
    field.options = enumValues.map((v) => ({ label: v, value: v }));
  }
  if (type === "number") {
    if (def.type === "integer") field.integer = true;
    if (def.minimum !== undefined) field.min = def.minimum;
    if (def.maximum !== undefined) field.max = def.maximum;
    if (def.exclusiveMinimum !== undefined) field.exclusiveMin = def.exclusiveMinimum;
    if (def.exclusiveMaximum !== undefined) field.exclusiveMax = def.exclusiveMaximum;
  }
  if (type === "tags") {
    const itemType = def.items ? resolveProperty(def.items).type : undefined;
    if (itemType === "number" || itemType === "integer") field.itemType = itemType;
  }
  return field;
}

export function fromEngineSchema(schema: EngineToolSchema): ToolUISchema {
  const properties = schema.input_schema?.properties ?? {};
  const required = new Set(schema.input_schema?.required ?? []);
  const mappedCat = mapCategory(schema.category ?? "");
  const meta = toolCategories.find((c) => c.id === mappedCat);

  const fields = Object.entries(properties).map(([name, def]) =>
    toField(name, def, required.has(name)),
  );

  return {
    toolName: schema.name,
    displayName: schema.name.replace(/([A-Z])/g, " $1").trim(),
    description: schema.description,
    category: mappedCat,
    icon: meta?.icon ?? "wrench",
    fields,
    outputType: "json",
  };
}

export const toolSchemas: ToolUISchema[] = [];

export function getToolSchema(toolName: string): ToolUISchema | null {
  return toolSchemas.find((s) => s.toolName === toolName) ?? null;
}

export function getCategoryMeta(categoryId: string): CategoryMeta {
  return (
    toolCategories.find((c) => c.id === categoryId) ?? {
      id: categoryId,
      label: categoryId,
      description: "",
      icon: "wrench",
      color: "slate",
      panelType: "generic",
    }
  );
}

export type { ToolCategory };
