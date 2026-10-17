import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ExternalLink, RefreshCw } from "lucide-react";
import { Label, BasicTextarea as Textarea } from "@ai-matrx/design-system";
import {
  builtinPromptRunText,
  builtinPrompts,
  systemPrompts,
  type SystemPrompt,
} from "@/lib/system-prompts";

// ── System Prompt Selector ────────────────────────────────────────────────
// A proper full-width selector for the settings panel.
// Shows a dropdown of all saved + builtin prompts, a textarea for the
// current content, and a link to the full System Prompts management page.

export function SystemPromptSelector({
  value,
  selectedId,
  onChange,
}: {
  value: string;
  /** The library prompt in use, by id; null when the text is the person's own. */
  selectedId: string | null;
  /** The new text and the library prompt it came from (null = typed by hand). */
  onChange: (content: string, promptId: string | null) => void;
}) {
  const navigate = useNavigate();
  const [userPrompts, setUserPrompts] = useState<SystemPrompt[]>(() =>
    systemPrompts.list(),
  );
  const [isRefreshing, setIsRefreshing] = useState(false);
  const allPrompts = [...builtinPrompts(), ...userPrompts];
  // By id, never by text: a built-in's run text is its FILLED Holder text,
  // which need not equal the template the list shows.
  const activePrompt = selectedId ? allPrompts.find((p) => p.id === selectedId) : undefined;

  const reload = () => {
    setUserPrompts(systemPrompts.list());
  };

  const handleRefresh = () => {
    setIsRefreshing(true);
    reload();
    setTimeout(() => setIsRefreshing(false), 600);
  };

  // Reload on mount and whenever prompts change (cross-tab storage event or same-tab custom event)
  useEffect(() => {
    reload();
    const handleStorage = (e: StorageEvent) => {
      if (e.key === "matrx-system-prompts") reload();
    };
    window.addEventListener("storage", handleStorage);
    window.addEventListener("matrx-prompts-changed", reload);
    return () => {
      window.removeEventListener("storage", handleStorage);
      window.removeEventListener("matrx-prompts-changed", reload);
    };
  }, []);

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <Label className="text-xs font-medium">System Prompt</Label>
        <div className="flex items-center gap-1.5">
          <button
            onClick={handleRefresh}
            title="Refresh prompt list"
            className="text-muted-foreground hover:text-foreground transition-colors"
          >
            <RefreshCw
              className={`h-3 w-3 ${isRefreshing ? "animate-spin" : ""}`}
            />
          </button>
          <button
            className="matrx-glyph-trim flex items-center gap-1 text-xs text-primary hover:underline"
            onClick={() => navigate("/system-prompts")}
            title="Open System Prompts library"
          >
            <ExternalLink className="h-3 w-3" />
            Manage
          </button>
        </div>
      </div>

      {/* Prompt picker dropdown */}
      <div className="space-y-1.5">
        <select
          className="w-full h-8 rounded-md border border-input bg-background px-2.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
          value={activePrompt?.id ?? "__custom__"}
          onChange={(e) => {
            if (e.target.value === "__custom__") return;
            if (e.target.value === "__manage__") {
              navigate("/system-prompts");
              return;
            }
            const found = allPrompts.find((p) => p.id === e.target.value);
            if (!found) return;
            if ("mandateKey" in found) {
              // A built-in runs with its Holder's text as the engine fills it,
              // asked only now that it is used.
              builtinPromptRunText(found.id)
                .then((text) => onChange(text ?? found.content, found.id))
                .catch((err) => console.warn("[system-prompts] built-in prompt unavailable:", err));
              return;
            }
            onChange(found.content, found.id);
          }}
        >
          <option value="__custom__">
            {activePrompt ? activePrompt.name : "— custom / paste below —"}
          </option>
          {userPrompts.length > 0 && (
            <optgroup label="My Prompts">
              {userPrompts.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </optgroup>
          )}
          <optgroup label="Built-in">
            {builtinPrompts().map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </optgroup>
          <option value="__manage__">→ Open Prompts Library…</option>
        </select>

        <Textarea
          value={value}
          onChange={(e) => onChange(e.target.value, null)}
          placeholder="You are a helpful assistant… (or select from the library above)"
          className="text-xs resize-none h-32"
        />
        <p className="text-[10px] text-muted-foreground text-right">
          {value.length} chars
        </p>
      </div>
    </div>
  );
}
