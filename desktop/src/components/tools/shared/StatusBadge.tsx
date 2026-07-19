import { cn } from "@/lib/utils";

type StatusType = "success" | "warning" | "error" | "info" | "neutral" | "running";

interface StatusBadgeProps {
  status: StatusType;
  label?: string;
  pulse?: boolean;
  className?: string;
}

const styles: Record<StatusType, { dot: string; bg: string; text: string }> = {
  success: { dot: "bg-emerald-500 dark:bg-emerald-400", bg: "bg-emerald-500/10 border-emerald-500/30", text: "text-emerald-700 dark:text-emerald-400" },
  warning: { dot: "bg-amber-500 dark:bg-amber-400", bg: "bg-amber-500/10 border-amber-500/30", text: "text-amber-700 dark:text-amber-400" },
  error: { dot: "bg-red-500 dark:bg-red-400", bg: "bg-red-500/10 border-red-500/30", text: "text-red-700 dark:text-red-400" },
  info: { dot: "bg-blue-500 dark:bg-blue-400", bg: "bg-blue-500/10 border-blue-500/30", text: "text-blue-700 dark:text-blue-400" },
  neutral: { dot: "bg-muted-foreground", bg: "bg-muted/40 border-border/50", text: "text-muted-foreground" },
  running: { dot: "bg-violet-400", bg: "bg-violet-500/10 border-violet-500/30", text: "text-violet-700 dark:text-violet-400" },
};

const defaultLabels: Record<StatusType, string> = {
  success: "Active",
  warning: "Warning",
  error: "Error",
  info: "Info",
  neutral: "Inactive",
  running: "Running",
};

export function StatusBadge({ status, label, pulse, className }: StatusBadgeProps) {
  const s = styles[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px] font-medium",
        s.bg,
        s.text,
        className,
      )}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", s.dot, pulse && "animate-pulse")} />
      {label ?? defaultLabels[status]}
    </span>
  );
}
