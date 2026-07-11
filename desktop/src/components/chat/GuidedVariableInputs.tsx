import { useState, useCallback } from "react";
import {
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  ChevronUp,
  Check,
  Minus,
  Plus,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { PromptVariable } from "@/types/agents";

// ============================================================================
// HELPERS
// ============================================================================

/** Turn snake_case or camelCase into "Human Label" */
function formatLabel(name: string): string {
  return name
    .replace(/_/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

// ============================================================================
// INLINE CUSTOM TEXT FALLBACK (shown below non-textarea inputs)
// ============================================================================

function InlineCustomInput({
  value,
  onChange,
  placeholder = "Or type your own answer…",
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <div className="mt-2 pt-2 border-t border-border/40">
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full text-sm bg-transparent border-none outline-none text-foreground placeholder:text-muted-foreground py-1 px-2 rounded"
      />
    </div>
  );
}

// ============================================================================
// GUIDED SUB-COMPONENTS
// ============================================================================

function GuidedTextarea({
  value,
  onChange,
  variableName,
}: {
  value: string;
  onChange: (v: string) => void;
  variableName: string;
}) {
  return (
    <textarea
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={`Type your ${variableName.toLowerCase()}…`}
      rows={3}
      className="w-full text-sm rounded-lg bg-muted border border-border px-3 py-2 text-foreground placeholder:text-muted-foreground outline-none resize-none focus:ring-1 focus:ring-primary/50"
      autoFocus
    />
  );
}

function GuidedSelect({
  value,
  onChange,
  options,
  allowOther,
  onAutoAdvance,
}: {
  value: string;
  onChange: (v: string) => void;
  options: string[];
  allowOther?: boolean;
  onAutoAdvance: () => void;
}) {
  const isOther = value.startsWith("Other: ");
  const [otherText, setOtherText] = useState(isOther ? value.slice(7) : "");
  const [showOther, setShowOther] = useState(isOther);
  const isCustom = !options.includes(value) && !isOther && value !== "";

  const handleSelect = (option: string) => {
    onChange(option);
    setShowOther(false);
    setTimeout(onAutoAdvance, 200);
  };

  const handleOtherClick = () => {
    setShowOther(true);
    onChange(`Other: ${otherText}`);
  };

  const handleCustomChange = (v: string) => {
    onChange(v === "" ? "" : v);
    setShowOther(false);
  };

  return (
    <div className="space-y-1.5">
      <div className="grid gap-1.5">
        {options.map((option) => {
          const isActive = value === option;
          return (
            <button
              key={option}
              type="button"
              onClick={() => handleSelect(option)}
              className={cn(
                "w-full text-left px-3 py-2.5 rounded-lg text-sm transition-all border",
                isActive
                  ? "bg-primary/10 border-primary text-foreground ring-1 ring-primary/30"
                  : "bg-muted border-border hover:bg-accent hover:border-foreground/20 text-foreground"
              )}
            >
              <span className="flex items-center gap-2">
                {isActive && (
                  <Check className="w-3.5 h-3.5 text-primary flex-shrink-0" />
                )}
                <span className={isActive ? "font-medium" : ""}>
                  {option || "(empty)"}
                </span>
              </span>
            </button>
          );
        })}
        {allowOther && (
          <button
            type="button"
            onClick={handleOtherClick}
            className={cn(
              "w-full text-left px-3 py-2.5 rounded-lg text-sm transition-all border",
              showOther
                ? "bg-primary/10 border-primary text-foreground ring-1 ring-primary/30"
                : "bg-muted border-border hover:bg-accent hover:border-foreground/20 text-foreground"
            )}
          >
            Other…
          </button>
        )}
      </div>
      {showOther && (
        <textarea
          value={otherText}
          onChange={(e) => {
            setOtherText(e.target.value);
            onChange(`Other: ${e.target.value}`);
          }}
          placeholder="Type your answer…"
          rows={2}
          className="w-full text-sm rounded-lg bg-muted border border-border px-3 py-2 text-foreground placeholder:text-muted-foreground outline-none resize-none focus:ring-1 focus:ring-primary/50 mt-1"
          autoFocus
        />
      )}
      {!showOther && (
        <InlineCustomInput
          value={isCustom ? value : ""}
          onChange={handleCustomChange}
        />
      )}
    </div>
  );
}

function GuidedCheckbox({
  value,
  onChange,
  options,
  allowOther,
}: {
  value: string;
  onChange: (v: string) => void;
  options: string[];
  allowOther?: boolean;
}) {
  const selected = value ? value.split("\n").filter(Boolean) : [];
  const otherItem = selected.find((s) => s.startsWith("Other: "));
  const [otherText, setOtherText] = useState(otherItem ? otherItem.slice(7) : "");
  const [showOther, setShowOther] = useState(!!otherItem);

  const toggle = (option: string) => {
    const regular = selected.filter((s) => !s.startsWith("Other: "));
    const has = regular.includes(option);
    const next = has ? regular.filter((s) => s !== option) : [...regular, option];
    const all =
      showOther && otherText ? [...next, `Other: ${otherText}`] : next;
    onChange(all.join("\n"));
  };

  const handleOtherToggle = () => {
    const regular = selected.filter((s) => !s.startsWith("Other: "));
    if (showOther) {
      setShowOther(false);
      onChange(regular.join("\n"));
    } else {
      setShowOther(true);
      onChange([...regular, `Other: ${otherText}`].join("\n"));
    }
  };

  return (
    <div className="space-y-1.5">
      <div className="grid gap-1.5">
        {options.map((option) => {
          const isActive = selected.includes(option);
          return (
            <button
              key={option}
              type="button"
              onClick={() => toggle(option)}
              className={cn(
                "w-full text-left px-3 py-2.5 rounded-lg text-sm transition-all border",
                isActive
                  ? "bg-primary/10 border-primary text-foreground ring-1 ring-primary/30"
                  : "bg-muted border-border hover:bg-accent hover:border-foreground/20 text-foreground"
              )}
            >
              <span className="flex items-center gap-2">
                <span
                  className={cn(
                    "flex items-center justify-center w-4 h-4 rounded-sm border flex-shrink-0",
                    isActive
                      ? "bg-primary border-primary text-primary-foreground"
                      : "border-primary"
                  )}
                >
                  {isActive && <Check className="w-3 h-3" />}
                </span>
                <span className={isActive ? "font-medium" : ""}>
                  {option || "(empty)"}
                </span>
              </span>
            </button>
          );
        })}
        {allowOther && (
          <button
            type="button"
            onClick={handleOtherToggle}
            className={cn(
              "w-full text-left px-3 py-2.5 rounded-lg text-sm transition-all border",
              showOther
                ? "bg-primary/10 border-primary text-foreground ring-1 ring-primary/30"
                : "bg-muted border-border hover:bg-accent hover:border-foreground/20 text-foreground"
            )}
          >
            <span className="flex items-center gap-2">
              <span
                className={cn(
                  "flex items-center justify-center w-4 h-4 rounded-sm border flex-shrink-0",
                  showOther
                    ? "bg-primary border-primary text-primary-foreground"
                    : "border-primary"
                )}
              >
                {showOther && <Check className="w-3 h-3" />}
              </span>
              <span>Other…</span>
            </span>
          </button>
        )}
      </div>
      {showOther && (
        <textarea
          value={otherText}
          onChange={(e) => {
            setOtherText(e.target.value);
            const regular = selected.filter((s) => !s.startsWith("Other: "));
            onChange([...regular, `Other: ${e.target.value}`].join("\n"));
          }}
          placeholder="Type your answer…"
          rows={2}
          className="w-full text-sm rounded-lg bg-muted border border-border px-3 py-2 text-foreground placeholder:text-muted-foreground outline-none resize-none mt-1"
          autoFocus
        />
      )}
      {selected.length > 0 && (
        <p className="text-xs text-muted-foreground">{selected.length} selected</p>
      )}
      {!showOther && (
        <InlineCustomInput value="" onChange={(v) => v && onChange(v)} />
      )}
    </div>
  );
}

function GuidedToggle({
  value,
  onChange,
  toggleValues,
  onAutoAdvance,
}: {
  value: string;
  onChange: (v: string) => void;
  toggleValues?: [string, string];
  onAutoAdvance: () => void;
}) {
  const [offLabel, onLabel] = toggleValues ?? ["No", "Yes"];

  const handleSelect = (val: string) => {
    onChange(val);
    setTimeout(onAutoAdvance, 200);
  };

  return (
    <div>
      <div className="grid grid-cols-2 gap-2">
        {[offLabel, onLabel].map((label) => {
          const isActive = value === label;
          return (
            <button
              key={label}
              type="button"
              onClick={() => handleSelect(label)}
              className={cn(
                "px-4 py-3 rounded-lg text-sm font-medium text-center transition-all border",
                isActive
                  ? "bg-primary/10 border-primary text-foreground ring-1 ring-primary/30"
                  : "bg-muted border-border hover:bg-accent hover:border-foreground/20 text-foreground"
              )}
            >
              {label}
            </button>
          );
        })}
      </div>
      <InlineCustomInput
        value={value !== offLabel && value !== onLabel ? value : ""}
        onChange={(v) => onChange(v || offLabel)}
      />
    </div>
  );
}

function GuidedNumber({
  value,
  onChange,
  min,
  max,
  step = 1,
}: {
  value: string;
  onChange: (v: string) => void;
  min?: number;
  max?: number;
  step?: number;
}) {
  const num = parseFloat(value) || 0;
  const canDec = min === undefined || num > min;
  const canInc = max === undefined || num < max;

  return (
    <div>
      <div className="flex items-center justify-center gap-3">
        <button
          type="button"
          onClick={() => canDec && onChange((num - step).toString())}
          disabled={!canDec}
          className="h-12 w-12 rounded-full border border-border flex items-center justify-center hover:bg-accent disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        >
          <Minus className="w-5 h-5" />
        </button>
        <input
          type="text"
          value={value}
          onChange={(e) => {
            const v = e.target.value;
            if (v === "" || v === "-" || !isNaN(parseFloat(v))) onChange(v);
          }}
          className="w-[120px] text-center text-2xl font-semibold h-12 rounded-lg bg-muted border border-border outline-none text-foreground"
          placeholder="0"
        />
        <button
          type="button"
          onClick={() => canInc && onChange((num + step).toString())}
          disabled={!canInc}
          className="h-12 w-12 rounded-full border border-border flex items-center justify-center hover:bg-accent disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        >
          <Plus className="w-5 h-5" />
        </button>
      </div>
      <InlineCustomInput
        value=""
        onChange={(v) => onChange(v)}
        placeholder="Or type a number…"
      />
    </div>
  );
}

// ============================================================================
// VARIABLE CONTENT RENDERER
// ============================================================================

function VariableContent({
  variable,
  value,
  onChange,
  onAutoAdvance,
}: {
  variable: PromptVariable;
  value: string;
  onChange: (v: string) => void;
  onAutoAdvance: () => void;
}) {
  const cc = variable.customComponent;

  if (!cc || cc.type === "textarea" || cc.type === "text") {
    return (
      <GuidedTextarea
        value={value}
        onChange={onChange}
        variableName={variable.name}
      />
    );
  }

  switch (cc.type) {
    case "select":
    case "radio":
      if (!cc.options?.length) {
        return (
          <GuidedTextarea
            value={value}
            onChange={onChange}
            variableName={variable.name}
          />
        );
      }
      return (
        <GuidedSelect
          key={variable.name}
          value={value}
          onChange={onChange}
          options={cc.options}
          {...(cc.allowOther !== undefined ? { allowOther: cc.allowOther } : {})}
          onAutoAdvance={onAutoAdvance}
        />
      );
    case "checkbox":
      if (!cc.options?.length) {
        return (
          <GuidedTextarea
            value={value}
            onChange={onChange}
            variableName={variable.name}
          />
        );
      }
      return (
        <GuidedCheckbox
          key={variable.name}
          value={value}
          onChange={onChange}
          options={cc.options}
          {...(cc.allowOther !== undefined ? { allowOther: cc.allowOther } : {})}
        />
      );
    case "toggle":
      return (
        <GuidedToggle
          value={value}
          onChange={onChange}
          {...(cc.toggleValues !== undefined ? { toggleValues: cc.toggleValues } : {})}
          onAutoAdvance={onAutoAdvance}
        />
      );
    case "number":
      return (
        <GuidedNumber
          value={value}
          onChange={onChange}
          {...(cc.min !== undefined ? { min: cc.min } : {})}
          {...(cc.max !== undefined ? { max: cc.max } : {})}
          {...(cc.step !== undefined ? { step: cc.step } : {})}
        />
      );
    default:
      return (
        <GuidedTextarea
          value={value}
          onChange={onChange}
          variableName={variable.name}
        />
      );
  }
}

// ============================================================================
// MAIN EXPORT
// ============================================================================

interface GuidedVariableInputsProps {
  variableDefaults: PromptVariable[];
  values: Record<string, string>;
  onChange: (name: string, value: string) => void;
  disabled?: boolean;
  /** Called with the final variable values when user submits */
  onSubmit?: (variables: Record<string, string>) => void;
  /** When true, renders with flat bottom for seamless join with chat input */
  seamless?: boolean;
}

export function GuidedVariableInputs({
  variableDefaults,
  values,
  onChange,
  disabled = false,
  onSubmit,
  seamless = false,
}: GuidedVariableInputsProps) {
  const [activeIndex, setActiveIndex] = useState(0);
  const [isCollapsed, setIsCollapsed] = useState(false);
  const total = variableDefaults.length;

  if (total === 0) return null;

  // Clamp: activeIndex survives agent switches (this component stays mounted
  // unkeyed in ChatPanel) — switching from a 5-variable agent at question 5
  // to a 2-variable agent indexed past the end and TypeError'd the chat page.
  const safeIndex = Math.min(activeIndex, total - 1);
  const variable = variableDefaults[safeIndex];
  const value = values[variable.name] ?? variable.defaultValue ?? "";
  const formattedName = formatLabel(variable.name);
  const helpText = variable.helpText;

  const answeredCount = variableDefaults.filter((v) => {
    const val = values[v.name] ?? v.defaultValue ?? "";
    return val.trim() !== "";
  }).length;

  const goNext = useCallback(() => {
    if (safeIndex < total - 1) setActiveIndex(safeIndex + 1);
  }, [safeIndex, total]);

  const goPrev = useCallback(() => {
    if (safeIndex > 0) setActiveIndex(safeIndex - 1);
  }, [safeIndex]);

  const handleSkipAll = useCallback(() => {
    setIsCollapsed(true);
  }, []);

  const handleChange = useCallback(
    (v: string) => {
      onChange(variable.name, v);
    },
    [onChange, variable.name]
  );

  const handleDone = () => {
    setIsCollapsed(true);
    onSubmit?.(values);
  };

  const outerRadius = seamless
    ? "rounded-t-xl rounded-b-none"
    : "rounded-xl";

  const progressDots = (
    <div className="flex items-center gap-1">
      {variableDefaults.map((v, i) => {
        const filled = (values[v.name] ?? v.defaultValue ?? "").trim() !== "";
        const isCurrent = i === safeIndex;
        return (
          <button
            key={v.name}
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setActiveIndex(i);
              if (isCollapsed) setIsCollapsed(false);
            }}
            className={cn(
              "rounded-full transition-all",
              isCurrent
                ? "w-5 h-2 bg-primary"
                : filled
                  ? "w-2 h-2 bg-primary/40"
                  : "w-2 h-2 bg-muted-foreground/20"
            )}
            title={formatLabel(v.name)}
          />
        );
      })}
    </div>
  );

  // ---- Collapsed ----
  if (isCollapsed) {
    return (
      <div
        className={cn(
          "bg-card border border-border",
          outerRadius,
          seamless && "border-b-0"
        )}
      >
        <button
          type="button"
          onClick={() => setIsCollapsed(false)}
          className={cn(
            "w-full flex items-center justify-between px-3 py-2 hover:bg-accent/50 transition-colors",
            outerRadius
          )}
        >
          <div className="flex items-center gap-2 min-w-0">
            {progressDots}
            <span className="text-xs text-muted-foreground truncate">
              {answeredCount}/{total} answered
            </span>
          </div>
          <ChevronDown className="w-3.5 h-3.5 text-muted-foreground flex-shrink-0" />
        </button>
      </div>
    );
  }

  // ---- Expanded ----
  return (
    <div
      className={cn(
        "bg-muted border border-border overflow-hidden",
        outerRadius,
        seamless && "border-b-0",
        disabled && "opacity-60 pointer-events-none"
      )}
    >
      {/* Header */}
      <div className="px-3 pt-3 pb-2">
        <div className="flex items-center justify-between mb-2">
          {progressDots}
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={handleSkipAll}
              className="text-xs text-muted-foreground/70 hover:text-foreground px-1.5 py-0.5 rounded transition-colors"
            >
              Skip
            </button>
            <button
              type="button"
              onClick={() => setIsCollapsed(true)}
              className="text-muted-foreground/70 hover:text-foreground p-0.5 rounded transition-colors"
              title="Collapse"
            >
              <ChevronUp className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

        <div className="mb-1">
          <h3 className="text-sm font-medium text-foreground">{formattedName}</h3>
          {helpText && (
            <p className="text-xs text-muted-foreground mt-0.5">{helpText}</p>
          )}
        </div>
      </div>

      {/* Content */}
      <div className="px-3 pb-2">
        <VariableContent
          variable={variable}
          value={value}
          onChange={handleChange}
          onAutoAdvance={goNext}
        />
      </div>

      {/* Navigation */}
      <div className="flex items-center justify-between px-3 py-2 border-t border-border/50">
        <button
          type="button"
          onClick={goPrev}
          disabled={safeIndex === 0}
          className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground disabled:opacity-30 disabled:cursor-default transition-colors px-1 py-0.5"
        >
          <ChevronLeft className="w-3.5 h-3.5" />
          Prev
        </button>

        <span className="text-[11px] text-muted-foreground">
          {safeIndex + 1} of {total} &middot; all optional
        </span>

        {safeIndex < total - 1 ? (
          <button
            type="button"
            onClick={goNext}
            className="flex items-center gap-1 text-xs text-primary hover:text-primary/80 transition-colors px-1 py-0.5 font-medium"
          >
            Next
            <ChevronRight className="w-3.5 h-3.5" />
          </button>
        ) : (
          <button
            type="button"
            onClick={handleDone}
            className="flex items-center gap-1 text-xs text-primary hover:text-primary/80 transition-colors px-1 py-0.5 font-medium"
          >
            Done
            <Check className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
    </div>
  );
}

export default GuidedVariableInputs;
