import { ExternalLink, RefreshCw } from "lucide-react";
import { useState, useEffect } from "react";
import { useLocation } from "react-router-dom";
import supabase from "@/lib/supabase";
import { getAppRuntimeConfig, getWebAppOrigin } from "@/lib/app-config";

const TARGET_PATH = "/demos/local-tools";
const HANDOFF_PATH = "/auth/desktop-handoff";

type DebugLog = { ok: boolean; msg: string };

async function buildIframeSrc(
  webOrigin: string,
  addLog: (ok: boolean, msg: string) => void,
): Promise<string> {
  addLog(true, "buildIframeSrc() called");

  addLog(true, "Calling supabase.auth.getSession()…");
  const {
    data: { session },
    error: sessionError,
  } = await supabase.auth.getSession();

  if (sessionError) {
    addLog(false, `getSession error: ${sessionError.message}`);
    return `${webOrigin}${TARGET_PATH}`;
  }

  if (!session) {
    addLog(
      false,
      "getSession returned null — no active session! User is not logged in to desktop app.",
    );
    return `${webOrigin}${TARGET_PATH}`;
  }

  addLog(true, `Session found — user: ${session.user?.email ?? "(no email)"}`);
  addLog(true, `access_token length: ${session.access_token?.length ?? 0}`);
  addLog(true, `refresh_token length: ${session.refresh_token?.length ?? 0}`);
  addLog(
    true,
    `expires_at: ${session.expires_at} (now: ${Math.floor(Date.now() / 1000)})`,
  );

  const expiresAt = session.expires_at ?? 0;
  const nowSeconds = Math.floor(Date.now() / 1000);
  const secondsUntilExpiry = expiresAt - nowSeconds;
  addLog(secondsUntilExpiry > 0, `Token expires in ${secondsUntilExpiry}s`);

  let finalSession = session;
  if (secondsUntilExpiry < 300) {
    addLog(true, "Token expires soon — calling refreshSession()…");
    const { data: refreshed, error: refreshError } =
      await supabase.auth.refreshSession();
    if (refreshError) {
      addLog(
        false,
        `refreshSession error: ${refreshError.message} — using existing token`,
      );
    } else if (refreshed.session) {
      finalSession = refreshed.session;
      addLog(
        true,
        `refreshSession OK — new token length: ${finalSession.access_token?.length}`,
      );
    }
  }

  const params = new URLSearchParams({
    access_token: finalSession.access_token,
    refresh_token: finalSession.refresh_token,
    redirect: TARGET_PATH,
  });

  const url = `${webOrigin}${HANDOFF_PATH}?${params.toString()}`;
  addLog(true, `Built handoff URL (first 120 chars): ${url.slice(0, 120)}…`);
  return url;
}

export function AiMatrx() {
  const [iframeSrc, setIframeSrc] = useState<string | null>(null);
  const [debugLogs, setDebugLogs] = useState<DebugLog[]>([]);
  const [building, setBuilding] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [showDebug, setShowDebug] = useState(true);
  const [webOrigin, setWebOrigin] = useState(() => getAppRuntimeConfig().webAppOrigin);
  const location = useLocation();
  const isVisible = location.pathname === "/aimatrx";

  const addLog = (ok: boolean, msg: string) =>
    setDebugLogs((prev) => [...prev, { ok, msg }]);

  useEffect(() => {
    if (!isVisible) return;
    setIframeSrc(null);
    setDebugLogs([]);
    setBuilding(true);
    getWebAppOrigin()
      .then((origin) => {
        setWebOrigin(origin);
        return buildIframeSrc(origin, addLog);
      })
      .then((src) => {
        setIframeSrc(src);
        addLog(true, `iframe src set — loading…`);
      })
      .catch((e) => addLog(false, `buildIframeSrc threw: ${String(e)}`))
      .finally(() => setBuilding(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reloadKey, isVisible]);

  const reload = () => {
    setIframeSrc(null);
    setReloadKey((k) => k + 1);
  };

  const hasError = debugLogs.some((l) => !l.ok);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        fontFamily: "monospace",
      }}
    >
      {/* Toolbar */}
      <div className="flex items-center gap-2 border-b bg-background/80 backdrop-blur px-4 py-2 shrink-0">
        <span className="text-xs font-medium text-muted-foreground truncate flex-1 select-text">
          {webOrigin}
          {TARGET_PATH}
        </span>
        <button
          onClick={() => setShowDebug((v) => !v)}
          title="Toggle debug panel"
          className="rounded border border-border bg-secondary px-1.5 py-0.5 text-[10px] text-secondary-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
        >
          {showDebug ? "Hide Debug" : "Show Debug"}
        </button>
        <button
          onClick={reload}
          disabled={building}
          title="Reload"
          className="flex h-6 w-6 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground transition-colors disabled:opacity-50"
        >
          <RefreshCw className="h-3.5 w-3.5" />
        </button>
        <a
          href={`${webOrigin}${TARGET_PATH}`}
          target="_blank"
          rel="noopener noreferrer"
          title="Open in browser"
          className="flex h-6 w-6 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
        >
          <ExternalLink className="h-3.5 w-3.5" />
        </a>
      </div>

      {/* Debug panel — shown while building or on error */}
      {showDebug && (building || debugLogs.length > 0) && (
        <div
          style={{
            background: "#0a0a0a",
            borderBottom: "1px solid #1f2937",
            padding: "10px 14px",
            maxHeight: "220px",
            overflowY: "auto",
            flexShrink: 0,
          }}
        >
          <div
            style={{
              color: "#60a5fa",
              fontSize: "11px",
              marginBottom: "6px",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
            }}
          >
            Desktop → Handoff Debug Log{" "}
            {hasError ? "⚠️ ERRORS" : building ? "⏳ building…" : "✓ done"}
          </div>
          {debugLogs.map((entry, i) => (
            <div
              key={i}
              style={{
                fontSize: "11px",
                marginBottom: "2px",
                display: "flex",
                gap: "6px",
              }}
            >
              <span
                style={{
                  color: entry.ok ? "#4ade80" : "#f87171",
                  flexShrink: 0,
                }}
              >
                {entry.ok ? "✓" : "✗"}
              </span>
              <span
                style={{
                  color: entry.ok ? "#d1d5db" : "#fca5a5",
                  wordBreak: "break-all",
                }}
              >
                {entry.msg}
              </span>
            </div>
          ))}
          {building && (
            <div style={{ fontSize: "11px", color: "#6b7280" }}>…</div>
          )}
        </div>
      )}

      {/* iframe */}
      {!building && iframeSrc && (
        <iframe
          key={reloadKey}
          src={iframeSrc}
          title="AiMatrx Local Tools"
          style={{ flex: 1, width: "100%", border: "none" }}
          allow="camera; microphone; clipboard-read; clipboard-write"
          sandbox="allow-same-origin allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox allow-top-navigation-by-user-activation"
        />
      )}
    </div>
  );
}
