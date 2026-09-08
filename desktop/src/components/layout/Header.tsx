import { Badge } from "@ai-matrx/design-system";
import type { EngineStatus } from "@/hooks/use-engine";

interface HeaderProps {
  title: string;
  description?: string;
  engineStatus: EngineStatus;
  engineUrl: string | null;
  children?: React.ReactNode;
}

const statusVariants: Record<
  EngineStatus,
  "success" | "warning" | "destructive" | "secondary"
> = {
  discovering: "warning",
  starting: "warning",
  connected: "success",
  disconnected: "secondary",
  error: "destructive",
};

const statusText: Record<EngineStatus, string> = {
  discovering: "Discovering",
  starting: "Starting",
  connected: "Connected",
  disconnected: "Offline",
  error: "Error",
};

export function Header({
  title,
  description,
  engineStatus,
  engineUrl,
  children,
}: HeaderProps) {
  return (
    <header className="no-select flex h-14 items-center justify-between border-b px-6">
      <div className="flex items-center gap-4">
        <div>
          <h1 className="text-lg font-semibold">{title}</h1>
          {description && (
            <p className="text-xs text-muted-foreground">{description}</p>
          )}
        </div>
      </div>
      <div className="flex items-center gap-3">
        {children}
        <div className="flex items-center gap-2">
          <Badge variant={statusVariants[engineStatus]}>
            {statusText[engineStatus]}
          </Badge>
          {engineUrl && (
            <span className="text-xs text-muted-foreground font-mono">
              {engineUrl.replace("http://", "")}
            </span>
          )}
        </div>
      </div>
    </header>
  );
}
