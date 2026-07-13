import { useState } from "react";
import { MediaThumb } from "@/components/media/MediaThumb";
import { Copy, Check, Clock } from "lucide-react";
import { cn } from "@/lib/utils";

interface OutputCardProps {
  title?: string;
  icon?: React.ReactNode;
  content: string;
  format?: "text" | "json" | "code" | "image";
  timestamp?: Date;
  copyable?: boolean;
  maxHeight?: number;
  className?: string;
  status?: "success" | "error" | "info";
  imageData?: string;
  imageMime?: string;
}

const statusStyles = {
  success: "border-emerald-500/30 bg-emerald-500/5",
  error: "border-red-500/30 bg-red-500/5",
  info: "border-border bg-card/50",
};

export function OutputCard({
  title,
  icon,
  content,
  format = "text",
  timestamp,
  copyable = true,
  maxHeight = 300,
  className,
  status = "info",
  imageData,
  imageMime = "image/png",
}: OutputCardProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const renderContent = () => {
    if (format === "image" && imageData) {
      // Canonical media: click → full-size viewer, right-click → the same
      // action menu as any other image in the app, hover → info + "⋯". A tool
      // output is not a lesser image than a generated one.
      return (
        <MediaThumb
          item={{
            id: `tool-output-${title ?? "image"}`,
            kind: "image",
            url: `data:${imageMime};base64,${imageData}`,
            itemId: null,
            source: "result",
            ...(title ? { fileName: `${title}.png` } : {}),
          }}
          variant="gallery"
          className="w-full rounded-lg"
        />
      );
    }

    if (format === "json") {
      try {
        const parsed = JSON.parse(content);
        return (
          <pre className="whitespace-pre-wrap break-words text-xs font-mono text-foreground">
            {JSON.stringify(parsed, null, 2)}
          </pre>
        );
      } catch {
        // Fall through to text rendering
      }
    }

    return (
      <pre
        className={cn(
          "whitespace-pre-wrap break-words text-xs font-mono",
          status === "error" ? "text-red-400" : "text-foreground",
        )}
      >
        {content}
      </pre>
    );
  };

  return (
    <div className={cn("rounded-xl border overflow-hidden", statusStyles[status], className)}>
      {/* Header */}
      {(title || copyable || timestamp) && (
        <div className="flex items-center justify-between border-b px-3 py-1.5 bg-muted/20">
          <div className="flex items-center gap-2 min-w-0">
            {icon}
            {title && (
              <span className="text-[11px] font-medium text-muted-foreground truncate">{title}</span>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {timestamp && (
              <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                <Clock className="h-2.5 w-2.5" />
                {timestamp.toLocaleTimeString()}
              </span>
            )}
            {copyable && content && (
              <button
                onClick={handleCopy}
                className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground transition-colors"
              >
                {copied ? <Check className="h-3 w-3 text-emerald-400" /> : <Copy className="h-3 w-3" />}
                {copied ? "Copied" : "Copy"}
              </button>
            )}
          </div>
        </div>
      )}

      {/* Body */}
      <div className="overflow-auto p-3" style={{ maxHeight }}>
        {renderContent()}
      </div>
    </div>
  );
}
