import * as React from "react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

type InputProps = Omit<
  React.ComponentProps<"input">,
  "type" | "value" | "onChange" | "inputMode"
>;

export interface NumberInputProps extends InputProps {
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  /** When set, parsing uses integers; otherwise inferred from step. */
  integer?: boolean;
  /**
   * Value committed on blur when the field is empty or invalid.
   * Defaults to the last committed value, then min, then 0.
   */
  emptyValue?: number;
  step?: number;
}

/** True for intermediate edit strings users type while entering a number. */
export function isNumberDraft(raw: string, integer: boolean): boolean {
  if (raw === "" || raw === "-") return true;
  if (!integer && (raw === "." || raw === "-.")) return true;
  if (integer) return /^-?\d*$/.test(raw);
  return /^-?\d*\.?\d*$/.test(raw);
}

/** Parse a draft; null means empty / incomplete / invalid. */
export function parseNumberDraft(raw: string, integer: boolean): number | null {
  const trimmed = raw.trim();
  if (
    trimmed === "" ||
    trimmed === "-" ||
    trimmed === "." ||
    trimmed === "-."
  ) {
    return null;
  }
  const n = integer ? Number.parseInt(trimmed, 10) : Number.parseFloat(trimmed);
  return Number.isFinite(n) ? n : null;
}

export function clampNumber(value: number, min?: number, max?: number): number {
  let next = value;
  if (min !== undefined && next < min) next = min;
  if (max !== undefined && next > max) next = max;
  return next;
}

function formatNumber(value: number, integer: boolean): string {
  if (!Number.isFinite(value)) return "";
  if (integer) return String(Math.trunc(value));
  return String(value);
}

/**
 * Number entry that allows a blank field while typing.
 *
 * Never bind a controlled `type="number"` input to a bare `number` and
 * coerce on every keystroke — clearing becomes impossible. Use this.
 */
export const NumberInput = React.forwardRef<HTMLInputElement, NumberInputProps>(
  (
    {
      value,
      onChange,
      min,
      max,
      step,
      integer,
      emptyValue,
      className,
      onFocus,
      onBlur,
      ...props
    },
    ref,
  ) => {
    const asInteger = integer ?? (step === undefined || Number.isInteger(step));
    const [focused, setFocused] = React.useState(false);
    const [text, setText] = React.useState(() =>
      formatNumber(value, asInteger),
    );

    React.useEffect(() => {
      if (!focused) {
        setText(formatNumber(value, asInteger));
      }
    }, [value, asInteger, focused]);

    const commit = React.useCallback(
      (raw: string) => {
        const parsed = parseNumberDraft(raw, asInteger);
        const fallback =
          emptyValue ??
          (Number.isFinite(value) ? value : undefined) ??
          min ??
          0;
        const next = clampNumber(
          parsed === null ? fallback : asInteger ? Math.trunc(parsed) : parsed,
          min,
          max,
        );
        onChange(next);
        setText(formatNumber(next, asInteger));
      },
      [asInteger, emptyValue, max, min, onChange, value],
    );

    return (
      <Input
        ref={ref}
        type="text"
        inputMode={asInteger ? "numeric" : "decimal"}
        value={focused ? text : formatNumber(value, asInteger)}
        onFocus={(event) => {
          setFocused(true);
          setText(formatNumber(value, asInteger));
          onFocus?.(event);
        }}
        onChange={(event) => {
          const raw = event.target.value;
          if (!isNumberDraft(raw, asInteger)) return;
          setText(raw);
          const parsed = parseNumberDraft(raw, asInteger);
          if (parsed !== null) {
            onChange(asInteger ? Math.trunc(parsed) : parsed);
          }
        }}
        onBlur={(event) => {
          setFocused(false);
          commit(text);
          onBlur?.(event);
        }}
        className={cn(className)}
        {...props}
      />
    );
  },
);
NumberInput.displayName = "NumberInput";
