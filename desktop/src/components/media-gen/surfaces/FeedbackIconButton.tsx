import type { LucideIcon } from "lucide-react";
import { Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

export function FeedbackIconButton({
  feedbackKey,
  activeKey,
  icon: Icon,
  activeIcon: ActiveIcon = Check,
  label,
  onClick,
  destructive,
}: {
  feedbackKey: string;
  activeKey: string | null;
  icon: LucideIcon;
  activeIcon?: LucideIcon;
  label: string;
  onClick: () => void | Promise<void>;
  destructive?: boolean;
}) {
  const active = activeKey === feedbackKey;
  const Active = active ? ActiveIcon : Icon;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className={`h-7 w-7 ${destructive ? "text-muted-foreground hover:text-destructive" : ""}`}
          onClick={() => void onClick()}
          aria-label={label}
        >
          <Active
            className={`h-3.5 w-3.5 ${active ? "text-green-600 dark:text-green-400" : ""}`}
          />
        </Button>
      </TooltipTrigger>
      <TooltipContent>{active ? "Done" : label}</TooltipContent>
    </Tooltip>
  );
}
