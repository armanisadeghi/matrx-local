import { cn } from "@/lib/utils";
import { categoryColorMap, getCategoryMeta } from "@/lib/tool-registry";
import * as LucideIcons from "lucide-react";
import type { LucideProps } from "lucide-react";
import type { ToolUISchema } from "@/types/tool-schema";

interface ToolCardProps {
  schema: ToolUISchema;
  isSelected: boolean;
  onClick: () => void;
}

function DynamicIcon({ name, ...props }: { name: string } & LucideProps) {
  const key = name
    .split("-")
    .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
    .join("");
  // @ts-expect-error dynamic lucide icon
  const Icon = LucideIcons[key] as React.ComponentType<LucideProps> | undefined;
  if (!Icon) return <LucideIcons.Wrench {...props} />;
  return <Icon {...props} />;
}

export function ToolCard({ schema, isSelected, onClick }: ToolCardProps) {
  const meta   = getCategoryMeta(schema.category);
  const colors = categoryColorMap[meta.color] ?? categoryColorMap["slate"]!;

  return (
    <button
      onClick={onClick}
      className={cn(
        "w-full text-left rounded-xl px-3 py-2.5 transition-all duration-150 group border",
        isSelected
          ? `${colors.bg} ${colors.border} shadow-sm`
          : "border-transparent hover:bg-muted/40 hover:border-border/50"
      )}
    >
      <div className="flex items-center gap-2.5 min-w-0">
        <div className={cn(
          "flex h-7 w-7 shrink-0 items-center justify-center rounded-lg transition-colors",
          isSelected ? `${colors.bg} ${colors.text}` : "bg-muted/60 text-muted-foreground group-hover:text-foreground"
        )}>
          <DynamicIcon name={schema.icon ?? meta.icon} className="h-3.5 w-3.5" />
        </div>
        <div className="min-w-0 flex-1">
          <p className={cn(
            "text-sm font-medium leading-none truncate",
            isSelected ? "text-foreground" : "text-foreground/80"
          )}>
            {schema.displayName}
          </p>
          {schema.description && (
            <p className="mt-1 text-[11px] text-muted-foreground line-clamp-1 leading-tight">
              {schema.description}
            </p>
          )}
        </div>
      </div>
    </button>
  );
}
