import { ExternalLink, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { Button } from "@ai-matrx/design-system";
import { getAppRuntimeConfig, getWebAppOrigin } from "@/lib/app-config";

const TARGET_PATH = "/demos/local-tools";

/** The embedded web app authenticates with its own browser session. */
export function localToolsIframeUrl(webOrigin: string): string {
  return `${webOrigin.replace(/\/$/, "")}${TARGET_PATH}`;
}

export function AiMatrx() {
  const [iframeSrc, setIframeSrc] = useState<string | null>(null);
  const [building, setBuilding] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [webOrigin, setWebOrigin] = useState(
    () => getAppRuntimeConfig().webAppOrigin,
  );
  const location = useLocation();
  const isVisible = location.pathname === "/aimatrx";
  const localToolsUrl = localToolsIframeUrl(webOrigin);

  useEffect(() => {
    if (!isVisible) return;
    setIframeSrc(null);
    setBuilding(true);
    void getWebAppOrigin()
      .then((origin) => {
        setWebOrigin(origin);
        setIframeSrc(localToolsIframeUrl(origin));
      })
      .finally(() => setBuilding(false));
  }, [reloadKey, isVisible]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b bg-background/80 px-4 py-2 backdrop-blur">
        <span className="flex-1 truncate text-xs font-medium text-muted-foreground select-text">
          {localToolsUrl}
        </span>
        <Button
          variant="ghost"
          size="icon"
          onClick={() => setReloadKey((key) => key + 1)}
          disabled={building}
          aria-label="Reload AI Matrx Local Tools"
        >
          <RefreshCw className="h-3.5 w-3.5" />
        </Button>
        <a
          href={localToolsUrl}
          target="_blank"
          rel="noopener noreferrer"
          aria-label="Open AI Matrx Local Tools in browser"
          className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          <ExternalLink className="h-3.5 w-3.5" />
        </a>
      </div>

      {building && (
        <div className="p-4 text-sm text-muted-foreground">Opening AI Matrx Local Tools…</div>
      )}

      {!building && iframeSrc && (
        <iframe
          key={reloadKey}
          src={iframeSrc}
          title="AiMatrx Local Tools"
          className="min-h-0 flex-1 border-0"
          allow="camera; microphone; clipboard-read; clipboard-write"
          sandbox="allow-same-origin allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox allow-top-navigation-by-user-activation"
        />
      )}
    </div>
  );
}
