import type {
  ToolCategory,
  ToolFieldSchema,
  ToolUISchema,
} from "@/types/tool-schema";

interface EngineToolSchema {
  name: string;
  description: string;
  category?: string;
  input_schema?: {
    properties?: Record<
      string,
      { type?: string; description?: string; default?: unknown }
    >;
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

export function fromEngineSchema(schema: EngineToolSchema): ToolUISchema {
  const properties = schema.input_schema?.properties ?? {};
  const required = new Set(schema.input_schema?.required ?? []);
  const mappedCat = mapCategory(schema.category ?? "");
  const meta = toolCategories.find((c) => c.id === mappedCat);

  const fields = Object.entries(properties).map(
    ([name, def]): ToolFieldSchema => ({
      name,
      label: toTitleCase(name),
      // Only treat *string* params as file paths — the name heuristic was
      // overriding ARRAY types (e.g. ArchiveCreate.source_paths), so the form
      // submitted a plain string the handler then iterated per character.
      type:
        name.toLowerCase().includes("path") &&
        (def.type === "string" || def.type === undefined)
          ? "file-path"
          : toFieldType(def.type),
      ...(def.description !== undefined ? { description: def.description } : {}),
      defaultValue: def.default,
      required: required.has(name),
      ...(name.includes("path") ? { placeholder: "/path/to/file" } : {}),
    }),
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
