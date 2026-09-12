import { Clock, RotateCcw, User } from "lucide-react";
import type { DocVersion } from "@/lib/api";
import { confirm } from "@ai-matrx/kit/confirm-opener";

interface VersionHistoryProps {
  versions: DocVersion[];
  onRevert: (versionNumber: number) => void;
}

export function VersionHistory({ versions, onRevert }: VersionHistoryProps) {
  if (versions.length === 0) {
    return (
      <div className="text-xs text-muted-foreground text-center py-4">
        No version history yet
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1">
      <h4 className="text-xs font-medium text-muted-foreground px-1 mb-1">
        Version History ({versions.length})
      </h4>
      {versions.map((v) => (
        <div
          key={v.id}
          className="flex items-start gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-accent group"
        >
          <Clock className="h-3 w-3 mt-0.5 shrink-0 text-muted-foreground" />
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1">
              <span className="font-medium">v{v.version_number}</span>
              <span className="text-muted-foreground">
                {new Date(v.created_at).toLocaleString()}
              </span>
            </div>
            <div className="flex items-center gap-1 text-muted-foreground mt-0.5">
              <User className="h-2.5 w-2.5" />
              <span>{v.change_source}</span>
              {v.change_type && <span>({v.change_type})</span>}
            </div>
          </div>
          <button
            onClick={async () => {
              const confirmed = await confirm({
                title: `Revert to version ${v.version_number}?`,
                description:
                  "This replaces the current text. A recoverable copy of the current text is not guaranteed.",
                confirmLabel: "Revert version",
                variant: "destructive",
              });
              if (!confirmed) return;
              onRevert(v.version_number);
            }}
            className="opacity-0 group-hover:opacity-100 rounded p-1 hover:bg-background transition-opacity"
            title="Revert to this version"
          >
            <RotateCcw className="h-3 w-3" />
          </button>
        </div>
      ))}
    </div>
  );
}
