function errorRecord(error: Error): Record<string, unknown> {
  const cause = (error as Error & { cause?: unknown }).cause;
  return {
    name: error.name,
    message: error.message,
    ...(error.stack ? { stack: error.stack } : {}),
    ...(cause !== undefined ? { cause } : {}),
  };
}

export function serializeLogValue(value: unknown): string {
  if (typeof value === "string") return value;

  const seen = new WeakSet<object>();
  try {
    const serialized = JSON.stringify(value, (_key, nested: unknown) => {
      if (nested instanceof Error) {
        if (seen.has(nested)) return "[Circular]";
        seen.add(nested);
        return errorRecord(nested);
      }
      if (typeof nested === "bigint") return nested.toString();
      if (nested && typeof nested === "object") {
        if (seen.has(nested)) return "[Circular]";
        seen.add(nested);
      }
      return nested;
    });
    return serialized ?? String(value);
  } catch {
    return String(value);
  }
}

export function formatConsoleArguments(args: unknown[]): string {
  return args.map(serializeLogValue).join(" ");
}
