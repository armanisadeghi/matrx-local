import { useState } from "react";
import {
  ChevronDown,
  ChevronUp,
  Info,
  Code2,
  List,
  Zap,
  Copy,
  CheckCheck,
  AlertCircle,
  CheckCircle2,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import type { ToolUISchema, ToolFieldSchema } from "@/types/tool-schema";

interface ToolInfoPanelProps {
  schema: ToolUISchema;
}

type Tab = "params" | "schema" | "examples";

const FIELD_TYPE_COLORS: Record<string, string> = {
  text: "bg-sky-500/15 text-sky-700 dark:text-sky-400 border-sky-500/30",
  textarea: "bg-indigo-500/15 text-indigo-700 dark:text-indigo-400 border-indigo-500/30",
  code: "bg-violet-500/15 text-violet-700 dark:text-violet-400 border-violet-500/30",
  number: "bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-500/30",
  boolean: "bg-teal-500/15 text-teal-700 dark:text-teal-400 border-teal-500/30",
  select: "bg-orange-500/15 text-orange-700 dark:text-orange-400 border-orange-500/30",
  "file-path": "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border-emerald-500/30",
  tags: "bg-rose-500/15 text-rose-700 dark:text-rose-400 border-rose-500/30",
  "key-value": "bg-purple-500/15 text-purple-700 dark:text-purple-400 border-purple-500/30",
  json: "bg-slate-500/15 text-slate-700 dark:text-slate-400 border-slate-500/30",
};

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1800);
      }}
      className="text-muted-foreground hover:text-foreground transition-colors"
    >
      {copied ? (
        <CheckCheck className="h-3.5 w-3.5 text-emerald-700 dark:text-emerald-400" />
      ) : (
        <Copy className="h-3.5 w-3.5" />
      )}
    </button>
  );
}

function ParamRow({ field }: { field: ToolFieldSchema }) {
  const typeStyle = FIELD_TYPE_COLORS[field.type] ?? FIELD_TYPE_COLORS["text"];
  return (
    <div className="rounded-lg border bg-card/30 p-3 space-y-2">
      <div className="flex items-start gap-2 flex-wrap">
        <code className="text-xs font-mono font-semibold text-foreground bg-muted/60 px-1.5 py-0.5 rounded">
          {field.name}
        </code>
        <span
          className={cn(
            "text-[10px] font-medium px-1.5 py-0.5 rounded border",
            typeStyle,
          )}
        >
          {field.type}
        </span>
        {field.required ? (
          <span className="flex items-center gap-1 text-[10px] font-medium text-destructive">
            <CheckCircle2 className="h-3 w-3" /> required
          </span>
        ) : (
          <span className="flex items-center gap-1 text-[10px] font-medium text-muted-foreground">
            <AlertCircle className="h-3 w-3" /> optional
          </span>
        )}
        {field.defaultValue !== undefined && (
          <span className="text-[10px] font-mono text-muted-foreground bg-muted/40 px-1.5 py-0.5 rounded border border-border/40">
            default: {JSON.stringify(field.defaultValue)}
          </span>
        )}
      </div>

      {field.description && (
        <p className="text-xs text-muted-foreground leading-relaxed">
          {field.description}
        </p>
      )}

      {(field.min !== undefined || field.max !== undefined) && (
        <p className="text-[10px] text-muted-foreground">
          Range:{" "}
          {field.min !== undefined && (
            <span className="font-mono">{field.min}</span>
          )}
          {field.min !== undefined && field.max !== undefined && " – "}
          {field.max !== undefined && (
            <span className="font-mono">{field.max}</span>
          )}
        </p>
      )}

      {field.options && field.options.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {field.options.map((opt) => (
            <span
              key={opt.value}
              className="text-[10px] font-mono bg-muted/40 border border-border/40 px-1.5 py-0.5 rounded"
            >
              {opt.value}
            </span>
          ))}
        </div>
      )}

      {field.placeholder && (
        <p className="text-[10px] text-muted-foreground">
          Example: <span className="font-mono">{field.placeholder}</span>
        </p>
      )}
    </div>
  );
}

function SchemaTab({ schema }: { schema: ToolUISchema }) {
  const jsonSchema = {
    name: schema.toolName,
    description: schema.description,
    category: schema.category,
    input_schema: {
      type: "object",
      properties: Object.fromEntries(
        schema.fields.map((f) => [
          f.name,
          {
            type:
              f.type === "number"
                ? "integer"
                : f.type === "boolean"
                  ? "boolean"
                  : "string",
            description: f.description,
            ...(f.defaultValue !== undefined
              ? { default: f.defaultValue }
              : {}),
            ...(f.min !== undefined ? { minimum: f.min } : {}),
            ...(f.max !== undefined ? { maximum: f.max } : {}),
          },
        ]),
      ),
      required: schema.fields.filter((f) => f.required).map((f) => f.name),
    },
  };
  const text = JSON.stringify(jsonSchema, null, 2);

  return (
    <div className="relative">
      <div className="absolute top-2 right-2 z-10">
        <CopyButton text={text} />
      </div>
      <ScrollArea className="h-[260px]">
        <pre className="p-3 text-[11px] font-mono text-foreground/90 whitespace-pre leading-relaxed">
          {text}
        </pre>
      </ScrollArea>
    </div>
  );
}

function ExamplesTab({ schema }: { schema: ToolUISchema }) {
  if (!schema.examples || schema.examples.length === 0) {
    // Auto-generate an example from required fields + defaults
    const exampleValues: Record<string, unknown> = {};
    for (const field of schema.fields) {
      if (field.required) {
        if (field.type === "number")
          exampleValues[field.name] = field.defaultValue ?? 0;
        else if (field.type === "boolean") exampleValues[field.name] = false;
        else if (field.type === "tags") exampleValues[field.name] = [];
        else exampleValues[field.name] = field.placeholder ?? `<${field.name}>`;
      } else if (field.defaultValue !== undefined) {
        exampleValues[field.name] = field.defaultValue;
      }
    }

    const text = JSON.stringify(exampleValues, null, 2);
    return (
      <div className="p-3 space-y-2">
        <p className="text-[10px] text-muted-foreground font-medium uppercase tracking-wider">
          Example invocation (required + defaults)
        </p>
        <div className="relative rounded-lg border bg-muted/20">
          <div className="absolute top-2 right-2">
            <CopyButton text={text} />
          </div>
          <pre className="p-3 text-[11px] font-mono text-foreground/90 whitespace-pre leading-relaxed">
            {text}
          </pre>
        </div>
      </div>
    );
  }

  return (
    <ScrollArea className="h-[260px]">
      <div className="p-3 space-y-3">
        {schema.examples.map((ex, i) => {
          const text = JSON.stringify(ex.values, null, 2);
          return (
            <div key={i} className="space-y-1.5">
              <p className="text-[11px] font-medium text-foreground">
                {ex.label}
              </p>
              <div className="relative rounded-lg border bg-muted/20">
                <div className="absolute top-2 right-2">
                  <CopyButton text={text} />
                </div>
                <pre className="p-3 text-[11px] font-mono text-foreground/90 whitespace-pre leading-relaxed">
                  {text}
                </pre>
              </div>
            </div>
          );
        })}
      </div>
    </ScrollArea>
  );
}

export function ToolInfoPanel({ schema }: ToolInfoPanelProps) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<Tab>("params");

  const tabs: { id: Tab; label: string; icon: React.ReactNode }[] = [
    {
      id: "params",
      label: "Parameters",
      icon: <List className="h-3.5 w-3.5" />,
    },
    {
      id: "schema",
      label: "JSON Schema",
      icon: <Code2 className="h-3.5 w-3.5" />,
    },
    {
      id: "examples",
      label: "Examples",
      icon: <Zap className="h-3.5 w-3.5" />,
    },
  ];

  return (
    <div className="border-t bg-card/20">
      {/* Toggle header */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-muted/20 transition-colors text-left group"
      >
        <div className="flex items-center gap-2">
          <Info className="h-3.5 w-3.5 text-muted-foreground group-hover:text-primary transition-colors" />
          <span className="text-xs font-medium text-muted-foreground group-hover:text-foreground transition-colors">
            Tool Reference
          </span>
          {schema.fields.length > 0 && (
            <Badge
              variant="secondary"
              className="text-[10px] h-4 px-1.5 tabular-nums"
            >
              {schema.fields.length} param
              {schema.fields.length !== 1 ? "s" : ""}
            </Badge>
          )}
        </div>
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
        ) : (
          <ChevronUp className="h-3.5 w-3.5 text-muted-foreground" />
        )}
      </button>

      {open && (
        <div className="border-t">
          {/* Tab bar */}
          <div className="flex min-w-0 overflow-x-auto border-b bg-muted/10 px-2 pt-1">
            {tabs.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => setTab(t.id)}
                className={cn(
                  "flex shrink-0 items-center gap-1.5 px-3 py-1.5 text-xs font-medium border-b-2 transition-all -mb-px",
                  tab === t.id
                    ? "border-primary text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground hover:border-border",
                )}
              >
                {t.icon}
                {t.label}
              </button>
            ))}
          </div>

          {/* Tab content */}
          {tab === "params" && (
            <ScrollArea className="h-[280px]">
              <div className="p-3 space-y-2">
                {schema.fields.length === 0 ? (
                  <p className="text-xs text-muted-foreground text-center py-8">
                    This tool takes no parameters.
                  </p>
                ) : (
                  schema.fields.map((field) => (
                    <ParamRow key={field.name} field={field} />
                  ))
                )}
              </div>
            </ScrollArea>
          )}

          {tab === "schema" && (
            <div className="rounded-b-lg overflow-hidden bg-muted/20">
              <SchemaTab schema={schema} />
            </div>
          )}

          {tab === "examples" && <ExamplesTab schema={schema} />}
        </div>
      )}
    </div>
  );
}
