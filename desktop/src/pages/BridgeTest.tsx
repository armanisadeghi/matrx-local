/**
 * Bridge Test — visual cockpit for the matrx-extend ↔ matrx-local bridge.
 *
 * Scope: drive every Phase 1+2 primitive from inside the desktop app
 * (no curl, no terminal). Five panels:
 *
 *   1. Engine self-check — health / version / capabilities via /extension/rpc
 *   2. Sessions          — live list of /extension/ws sessions with disconnect
 *   3. Invoke            — engine→browser dispatch via /extension/invoke
 *   4. Broadcast plumb   — feature-flag status + test publish
 *   5. Live event log    — fan-out from /extension/bridge-events WS
 *
 * All polling/WS hooks are gated on the page being mounted. The sessions
 * list polls every 2s; the live event log subscribes to a dedicated WS.
 *
 * Conventions followed (per CLAUDE.md):
 *   - actions wrapped in useMemo
 *   - useEffect deps name specific stable callbacks, never an "actions" object
 *   - polling intervals depend on narrow booleans, never broad objects
 *   - production-grade only — no stubs / placeholders
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import {
  Activity as ActivityIcon,
  AlertCircle,
  Check,
  ChevronRight,
  Cloud,
  Gauge,
  Heart,
  ListTree,
  Pause,
  Play,
  Plug,
  Power,
  Radio,
  RefreshCw,
  Send,
  ShieldCheck,
  Trash2,
  X,
} from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import {
  engine,
  type BridgeEvent,
  type ExtensionBootCheckSummary,
  type ExtensionBroadcastStatus,
  type ExtensionCommandMetrics,
  type ExtensionInvokeResponse,
  type ExtensionMetricsSnapshot,
  type ExtensionRpcResponse,
  type ExtensionSessionInfo,
  type ExtensionTunnelStatus,
} from "@/lib/api";
import type { EngineStatus } from "@/hooks/use-engine";
import type { User as SupabaseUser } from "@supabase/supabase-js";

interface BridgeTestProps {
  engineStatus: EngineStatus;
  engineUrl: string | null;
  user: SupabaseUser | null;
}

// Common browser tools the agent ships with — preset suggestions for the
// invoke panel. Not exhaustive; users can type any tool name.
const TOOL_PRESETS = [
  "take_screenshot",
  "read_page",
  "find",
  "get_active_tab",
  "list_open_tabs",
  "click_element",
  "type_into_element",
  "navigate_active_tab",
  "scroll_page",
  "ask_user",
];

const MAX_LOG_LINES = 500;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatRelative(secsSinceEpoch: number): string {
  const ageSec = Math.max(0, Math.round(Date.now() / 1000 - secsSinceEpoch));
  if (ageSec < 60) return `${ageSec}s ago`;
  if (ageSec < 3600) return `${Math.floor(ageSec / 60)}m ${ageSec % 60}s ago`;
  return `${Math.floor(ageSec / 3600)}h ago`;
}

function shortId(id: string): string {
  if (id.length <= 12) return id;
  return `${id.slice(0, 8)}…${id.slice(-4)}`;
}

function formatJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function levelClassFor(direction: BridgeEvent["direction"]): string {
  if (direction === "in") return "text-emerald-700 dark:text-emerald-400";
  if (direction === "out") return "text-sky-700 dark:text-sky-400";
  return "text-zinc-400";
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function BridgeTest({ engineStatus, engineUrl, user }: BridgeTestProps) {
  // Panel 1 — Engine self-check ---------------------------------------------
  const [healthResult, setHealthResult] = useState<ExtensionRpcResponse | null>(
    null,
  );
  const [versionResult, setVersionResult] =
    useState<ExtensionRpcResponse | null>(null);
  const [capsResult, setCapsResult] = useState<ExtensionRpcResponse | null>(
    null,
  );
  const [busyCmd, setBusyCmd] = useState<string | null>(null);

  // Panel 2 — Sessions -------------------------------------------------------
  const [sessions, setSessions] = useState<ExtensionSessionInfo[]>([]);
  const [sessionsError, setSessionsError] = useState<string | null>(null);
  const [autoRefreshSessions, setAutoRefreshSessions] = useState(true);

  // Panel 3 — Invoke ---------------------------------------------------------
  const [invokeSessionId, setInvokeSessionId] = useState<string>("");
  const [invokeToolName, setInvokeToolName] = useState("take_screenshot");
  const [invokeArgs, setInvokeArgs] = useState("{}");
  const [invokeTimeout, setInvokeTimeout] = useState(30);
  const [invokeBusy, setInvokeBusy] = useState(false);
  const [invokeResult, setInvokeResult] =
    useState<ExtensionInvokeResponse | null>(null);
  const [invokeArgsError, setInvokeArgsError] = useState<string | null>(null);

  // Panel 4 — Broadcast plumb -----------------------------------------------
  const [broadcastStatus, setBroadcastStatus] =
    useState<ExtensionBroadcastStatus | null>(null);
  const [broadcastBusy, setBroadcastBusy] = useState(false);
  const [broadcastResult, setBroadcastResult] = useState<{
    ok: boolean;
    sent: boolean;
    enabled: boolean;
    timestamp: number;
  } | null>(null);
  const [broadcastError, setBroadcastError] = useState<string | null>(null);

  // Panel 5 — Live event log -------------------------------------------------
  const [logEvents, setLogEvents] = useState<BridgeEvent[]>([]);
  const [logPaused, setLogPaused] = useState(false);
  const [logConnected, setLogConnected] = useState(false);
  const logPausedRef = useRef(logPaused);
  logPausedRef.current = logPaused;

  // Metrics (lives inside Panel 1 as a sub-section) --------------------------
  const [metrics, setMetrics] = useState<ExtensionMetricsSnapshot>({});
  const [metricsError, setMetricsError] = useState<string | null>(null);
  const [metricsBusy, setMetricsBusy] = useState(false);

  // Tunnel status (sub-section of Panel 1) ----------------------------------
  const [tunnelStatus, setTunnelStatus] =
    useState<ExtensionTunnelStatus | null>(null);
  const [tunnelStatusError, setTunnelStatusError] = useState<string | null>(
    null,
  );
  const [tunnelStatusBusy, setTunnelStatusBusy] = useState(false);

  // Boot self-check (sub-section of Panel 1) --------------------------------
  // Cached at engine startup; the "Re-run self-check" button triggers a
  // live re-run via POST /extension/boot-check/run. Both endpoints share
  // the same wire shape so we render the same component for either path.
  const [bootCheck, setBootCheck] = useState<ExtensionBootCheckSummary | null>(
    null,
  );
  const [bootCheckError, setBootCheckError] = useState<string | null>(null);
  const [bootCheckBusy, setBootCheckBusy] = useState(false);

  const isEngineReady = engineStatus === "connected" && engineUrl !== null;
  const location = useLocation();
  const isPageVisible = location.pathname === "/bridge-test";

  // -------------------------------------------------------------------------
  // Stable callbacks (each defined once with useCallback so the actions
  // memo and effect deps stay narrow).
  // -------------------------------------------------------------------------

  const runHealth = useCallback(async () => {
    if (!isEngineReady) return;
    setBusyCmd("health");
    try {
      setHealthResult(await engine.extensionRpc("health"));
    } catch (e) {
      setHealthResult({ ok: false, error: String(e) });
    } finally {
      setBusyCmd(null);
    }
  }, [isEngineReady]);

  const runVersion = useCallback(async () => {
    if (!isEngineReady) return;
    setBusyCmd("version");
    try {
      setVersionResult(await engine.extensionRpc("version"));
    } catch (e) {
      setVersionResult({ ok: false, error: String(e) });
    } finally {
      setBusyCmd(null);
    }
  }, [isEngineReady]);

  const runCapabilities = useCallback(async () => {
    if (!isEngineReady) return;
    setBusyCmd("capabilities");
    try {
      setCapsResult(await engine.extensionRpc("capabilities"));
    } catch (e) {
      setCapsResult({ ok: false, error: String(e) });
    } finally {
      setBusyCmd(null);
    }
  }, [isEngineReady]);

  const refreshSessions = useCallback(async () => {
    if (!isEngineReady) return;
    try {
      const res = await engine.extensionListSessions();
      setSessions(res.sessions);
      setSessionsError(null);
    } catch (e) {
      setSessionsError(String(e));
    }
  }, [isEngineReady]);

  const disconnectSession = useCallback(
    async (sessionId: string) => {
      if (!isEngineReady) return;
      try {
        await engine.extensionDisconnectSession(
          sessionId,
          "Closed from Bridge Test panel",
        );
        await refreshSessions();
      } catch (e) {
        setSessionsError(String(e));
      }
    },
    [isEngineReady, refreshSessions],
  );

  const runInvoke = useCallback(async () => {
    if (!isEngineReady) return;
    if (!invokeSessionId) {
      setInvokeResult({ ok: false, error: "Select a session first" });
      return;
    }
    if (!invokeToolName.trim()) {
      setInvokeResult({ ok: false, error: "Tool name is required" });
      return;
    }
    let parsed: Record<string, unknown> = {};
    try {
      parsed = invokeArgs.trim() ? JSON.parse(invokeArgs) : {};
      setInvokeArgsError(null);
    } catch (e) {
      setInvokeArgsError(`Args is not valid JSON: ${e}`);
      return;
    }
    setInvokeBusy(true);
    setInvokeResult(null);
    try {
      const res = await engine.extensionInvoke(
        invokeSessionId,
        invokeToolName.trim(),
        parsed,
        invokeTimeout,
      );
      setInvokeResult(res);
    } catch (e) {
      setInvokeResult({ ok: false, error: String(e) });
    } finally {
      setInvokeBusy(false);
    }
  }, [
    isEngineReady,
    invokeSessionId,
    invokeToolName,
    invokeArgs,
    invokeTimeout,
  ]);

  const refreshBroadcastStatus = useCallback(async () => {
    if (!isEngineReady) return;
    try {
      setBroadcastStatus(await engine.extensionBroadcastStatus());
    } catch {
      /* non-critical — UI shows "unknown" */
    }
  }, [isEngineReady]);

  const sendBroadcastTest = useCallback(async () => {
    if (!isEngineReady) return;
    if (!user?.id) return;
    setBroadcastBusy(true);
    setBroadcastError(null);
    try {
      const res = await engine.extensionBroadcastTest(user.id, "bridge.test", {
        from: "desktop-bridge-test-panel",
        timestamp: Date.now(),
      });
      setBroadcastResult({ ...res, timestamp: Date.now() });
    } catch (e) {
      setBroadcastResult({
        ok: false,
        sent: false,
        enabled: false,
        timestamp: Date.now(),
      });
      // Dedicated state — routing this into sessionsError rendered the
      // failure in the Sessions panel and the 2s session poll cleared it.
      setBroadcastError(String(e));
    } finally {
      setBroadcastBusy(false);
    }
  }, [isEngineReady, user?.id]);

  const clearLog = useCallback(() => {
    setLogEvents([]);
  }, []);

  const refreshMetrics = useCallback(async () => {
    if (!isEngineReady) return;
    try {
      const snap = await engine.extensionGetMetrics();
      setMetrics(snap);
      setMetricsError(null);
    } catch (e) {
      setMetricsError(String(e));
    }
  }, [isEngineReady]);

  const resetMetrics = useCallback(async () => {
    if (!isEngineReady) return;
    setMetricsBusy(true);
    try {
      await engine.extensionResetMetrics();
      await refreshMetrics();
    } catch (e) {
      setMetricsError(String(e));
    } finally {
      setMetricsBusy(false);
    }
  }, [isEngineReady, refreshMetrics]);

  const refreshTunnelStatus = useCallback(async () => {
    if (!isEngineReady) return;
    setTunnelStatusBusy(true);
    try {
      const snap = await engine.extensionTunnelStatus();
      setTunnelStatus(snap);
      setTunnelStatusError(null);
    } catch (e) {
      setTunnelStatusError(String(e));
    } finally {
      setTunnelStatusBusy(false);
    }
  }, [isEngineReady]);

  // Boot self-check — initial fetch reads the engine-side cache; the
  // re-run callback hits POST /extension/boot-check/run which both
  // refreshes the cache and returns the new summary.
  const refreshBootCheck = useCallback(async () => {
    if (!isEngineReady) return;
    try {
      const summary = await engine.extensionBootCheckGet();
      setBootCheck(summary);
      setBootCheckError(null);
    } catch (e) {
      setBootCheckError(String(e));
    }
  }, [isEngineReady]);

  const rerunBootCheck = useCallback(async () => {
    if (!isEngineReady) return;
    setBootCheckBusy(true);
    try {
      const summary = await engine.extensionBootCheckRun();
      setBootCheck(summary);
      setBootCheckError(null);
    } catch (e) {
      setBootCheckError(String(e));
    } finally {
      setBootCheckBusy(false);
    }
  }, [isEngineReady]);

  // -------------------------------------------------------------------------
  // Effects — narrow deps, no broad "actions" objects
  // -------------------------------------------------------------------------

  // Initial loads when engine becomes ready
  useEffect(() => {
    if (!isEngineReady) return;
    void refreshSessions();
    void refreshBroadcastStatus();
    void refreshMetrics();
    void refreshTunnelStatus();
    void refreshBootCheck();
  }, [
    isEngineReady,
    refreshSessions,
    refreshBroadcastStatus,
    refreshMetrics,
    refreshTunnelStatus,
    refreshBootCheck,
  ]);

  // Metrics polling — gated on document visibility so we don't burn
  // cycles when the desktop window is minimized or the page is hidden.
  useEffect(() => {
    if (!isPageVisible || !isEngineReady) return;
    let active = !document.hidden;
    let id: ReturnType<typeof setInterval> | null = null;

    const start = () => {
      if (id !== null) return;
      id = setInterval(() => void refreshMetrics(), 2000);
    };
    const stop = () => {
      if (id !== null) {
        clearInterval(id);
        id = null;
      }
    };

    const onVisibility = () => {
      const visible = !document.hidden;
      if (visible && !active) void refreshMetrics();
      active = visible;
      if (visible) start();
      else stop();
    };

    if (active) start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      stop();
    };
  }, [isPageVisible, isEngineReady, refreshMetrics]);

  // Sessions auto-refresh — gated on the narrow boolean, not actions
  useEffect(() => {
    // Keep-alive pages stay mounted forever — gate on the route actually
    // being visible so this 2s poll doesn't hit the engine for the whole
    // app lifetime from a hidden page.
    if (!isPageVisible || !isEngineReady || !autoRefreshSessions) return;
    const id = setInterval(() => void refreshSessions(), 2000);
    return () => clearInterval(id);
  }, [isPageVisible, isEngineReady, autoRefreshSessions, refreshSessions]);

  // Default the invoke session dropdown to the first available session
  // whenever the list changes and the current selection is no longer valid.
  useEffect(() => {
    if (sessions.length === 0) {
      if (invokeSessionId) setInvokeSessionId("");
      return;
    }
    const stillValid = sessions.some((s) => s.session_id === invokeSessionId);
    const firstSession = sessions[0];
    if (!stillValid && firstSession) {
      setInvokeSessionId(firstSession.session_id);
    }
  }, [sessions, invokeSessionId]);

  // Live event log subscription
  useEffect(() => {
    if (!isEngineReady) return;
    let teardown: (() => void) | null = null;
    try {
      teardown = engine.subscribeBridgeEvents(
        (event) => {
          if (logPausedRef.current) return;
          setLogEvents((prev) => {
            const next = [...prev, event];
            if (next.length > MAX_LOG_LINES) {
              return next.slice(next.length - MAX_LOG_LINES);
            }
            return next;
          });
        },
        () => setLogConnected(false),
        // Drive the badge from the REAL socket lifecycle — setting it true
        // synchronously claimed "streaming" before any connection existed,
        // and a clean close (engine restart) left it stuck on forever.
        () => setLogConnected(true),
        () => setLogConnected(false),
      );
    } catch {
      setLogConnected(false);
    }
    return () => {
      try {
        teardown?.();
      } catch {
        /* ignore */
      }
      setLogConnected(false);
    };
  }, [isEngineReady]);

  // -------------------------------------------------------------------------
  // Derived
  // -------------------------------------------------------------------------

  const capabilityCount = useMemo(() => {
    if (!capsResult?.ok) return null;
    const data = capsResult.data as { tools?: unknown[] } | undefined;
    return Array.isArray(data?.tools) ? data!.tools!.length : null;
  }, [capsResult]);

  const capabilityToolNames = useMemo(() => {
    if (!capsResult?.ok) return [] as string[];
    const data = capsResult.data as
      | { tools?: Array<{ name?: string }> }
      | undefined;
    return Array.isArray(data?.tools)
      ? data!.tools!.map((t) => t.name ?? "<unnamed>")
      : [];
  }, [capsResult]);

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Bridge Test"
        description="Drive the matrx-extend ↔ matrx-local bridge from inside the desktop app."
      >
        {!isEngineReady && (
          <Badge variant="destructive" className="text-xs">
            Engine not connected
          </Badge>
        )}
        {isEngineReady && (
          <Badge variant="secondary" className="text-xs">
            {engineUrl}
          </Badge>
        )}
      </PageHeader>

      <ScrollArea className="flex-1">
        <div className="mx-auto flex max-w-5xl flex-col gap-4 p-6">
          {/* Panel 1 — Engine self-check ------------------------------------ */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-sm">
                <Heart className="h-4 w-4 text-rose-700 dark:text-rose-400" />
                Engine self-check
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <span>Resolved engine port:</span>
                <code className="rounded bg-muted px-1.5 py-0.5 font-mono">
                  {engineUrl ?? "(not discovered)"}
                </code>
              </div>

              <PairingSection isEngineReady={isEngineReady} />

              <Separator />

              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!isEngineReady || busyCmd === "health"}
                  onClick={runHealth}
                >
                  {busyCmd === "health" ? (
                    <RefreshCw className="mr-2 h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Heart className="mr-2 h-3.5 w-3.5" />
                  )}
                  Health
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!isEngineReady || busyCmd === "version"}
                  onClick={runVersion}
                >
                  {busyCmd === "version" ? (
                    <RefreshCw className="mr-2 h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <ChevronRight className="mr-2 h-3.5 w-3.5" />
                  )}
                  Version
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!isEngineReady || busyCmd === "capabilities"}
                  onClick={runCapabilities}
                >
                  {busyCmd === "capabilities" ? (
                    <RefreshCw className="mr-2 h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <ListTree className="mr-2 h-3.5 w-3.5" />
                  )}
                  Capabilities
                </Button>
              </div>

              <div className="grid gap-3 lg:grid-cols-3">
                <RpcResultCard label="Health" result={healthResult} />
                <RpcResultCard label="Version" result={versionResult} />
                <CapabilitiesCard
                  result={capsResult}
                  count={capabilityCount}
                  names={capabilityToolNames}
                />
              </div>

              <Separator />

              <MetricsSection
                metrics={metrics}
                error={metricsError}
                busy={metricsBusy}
                isEngineReady={isEngineReady}
                onRefresh={refreshMetrics}
                onReset={resetMetrics}
              />

              <Separator />

              <TunnelStatusSection
                status={tunnelStatus}
                error={tunnelStatusError}
                busy={tunnelStatusBusy}
                isEngineReady={isEngineReady}
                onRefresh={refreshTunnelStatus}
              />

              <Separator />

              <BootCheckSection
                summary={bootCheck}
                error={bootCheckError}
                busy={bootCheckBusy}
                isEngineReady={isEngineReady}
                onRerun={rerunBootCheck}
              />
            </CardContent>
          </Card>

          {/* Panel 2 — Sessions --------------------------------------------- */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-sm">
                <Plug className="h-4 w-4 text-emerald-700 dark:text-emerald-400" />
                Live extension sessions
                <Badge variant="secondary" className="ml-1 text-[10px]">
                  {sessions.length}
                </Badge>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Switch
                    checked={autoRefreshSessions}
                    onCheckedChange={setAutoRefreshSessions}
                    id="auto-refresh-sessions"
                  />
                  <Label
                    htmlFor="auto-refresh-sessions"
                    className="text-xs text-muted-foreground"
                  >
                    Auto-refresh every 2s
                  </Label>
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={!isEngineReady}
                  onClick={refreshSessions}
                >
                  <RefreshCw className="mr-2 h-3.5 w-3.5" />
                  Refresh now
                </Button>
              </div>

              {sessionsError && (
                <div className="flex items-center gap-2 rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-700 dark:text-red-300">
                  <AlertCircle className="h-3.5 w-3.5" />
                  {sessionsError}
                </div>
              )}

              {sessions.length === 0 ? (
                <div className="rounded border border-dashed border-muted-foreground/30 px-3 py-6 text-center text-xs text-muted-foreground">
                  No live extension sessions. Installed or inactive Chrome
                  profiles cannot be discovered until their extension service
                  worker connects.
                </div>
              ) : (
                <div className="overflow-hidden rounded border">
                  <table className="w-full text-xs">
                    <thead className="bg-muted/50 text-muted-foreground">
                      <tr>
                        <th className="px-3 py-2 text-left font-medium">
                          Extension instance
                        </th>
                        <th className="px-3 py-2 text-left font-medium">
                          Connected
                        </th>
                        <th className="px-3 py-2 text-left font-medium">
                          Last ping
                        </th>
                        <th className="px-3 py-2 text-left font-medium">
                          Pending
                        </th>
                        <th className="px-3 py-2 text-right font-medium">
                          Action
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {sessions.map((s) => (
                        <tr
                          key={s.session_id}
                          className="border-t hover:bg-muted/30"
                        >
                          <td
                            className="px-3 py-2 font-mono"
                            title={s.session_id}
                          >
                            <div>{s.extension_name ?? "Unidentified client"}</div>
                            <div className="text-[10px] text-muted-foreground">
                              {s.extension_id
                                ? `${shortId(s.extension_id)}${s.extension_version ? ` · v${s.extension_version}` : ""}`
                                : `session ${shortId(s.session_id)}`}
                            </div>
                          </td>
                          <td className="px-3 py-2 text-muted-foreground">
                            {formatRelative(s.connected_at)}
                          </td>
                          <td className="px-3 py-2 text-muted-foreground">
                            {formatRelative(s.last_seen_at)}
                          </td>
                          <td className="px-3 py-2 text-muted-foreground">
                            {s.pending_calls}
                          </td>
                          <td className="px-3 py-2 text-right">
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => disconnectSession(s.session_id)}
                            >
                              <Power className="mr-1 h-3 w-3" />
                              Disconnect
                            </Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Panel 3 — Invoke ----------------------------------------------- */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-sm">
                <Send className="h-4 w-4 text-sky-700 dark:text-sky-400" />
                Invoke extension tool
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid gap-3 md:grid-cols-2">
                <div className="space-y-1.5">
                  <Label className="text-xs">Target session</Label>
                  <Select
                    {...(invokeSessionId ? { value: invokeSessionId } : {})}
                    onValueChange={setInvokeSessionId}
                    disabled={sessions.length === 0}
                  >
                    <SelectTrigger>
                      <SelectValue
                        placeholder={
                          sessions.length === 0
                            ? "No sessions connected"
                            : "Select a session"
                        }
                      />
                    </SelectTrigger>
                    <SelectContent>
                      {sessions.map((s) => (
                        <SelectItem
                          key={s.session_id}
                          value={s.session_id}
                          className="font-mono"
                        >
                          {shortId(s.session_id)} ·{" "}
                          {formatRelative(s.connected_at)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-1.5">
                  <Label className="text-xs">Tool name</Label>
                  <Input
                    value={invokeToolName}
                    onChange={(e) => setInvokeToolName(e.target.value)}
                    placeholder="take_screenshot"
                    className="font-mono"
                    list="tool-presets"
                  />
                  <datalist id="tool-presets">
                    {TOOL_PRESETS.map((t) => (
                      <option key={t} value={t} />
                    ))}
                  </datalist>
                </div>
              </div>

              <div className="space-y-1.5">
                <Label className="text-xs">Args (JSON)</Label>
                <Textarea
                  value={invokeArgs}
                  onChange={(e) => setInvokeArgs(e.target.value)}
                  rows={5}
                  className="font-mono text-xs"
                  placeholder="{}"
                />
                {invokeArgsError && (
                  <p className="text-xs text-red-700 dark:text-red-400">{invokeArgsError}</p>
                )}
              </div>

              <div className="flex items-end justify-between gap-3">
                <div className="space-y-1.5">
                  <Label className="text-xs">Timeout (s)</Label>
                  <Input
                    type="number"
                    min={1}
                    max={120}
                    value={invokeTimeout}
                    onChange={(e) =>
                      setInvokeTimeout(Number(e.target.value) || 30)
                    }
                    className="w-24"
                  />
                </div>
                <Button
                  disabled={!isEngineReady || invokeBusy || !invokeSessionId}
                  onClick={runInvoke}
                >
                  {invokeBusy ? (
                    <RefreshCw className="mr-2 h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Send className="mr-2 h-3.5 w-3.5" />
                  )}
                  Invoke
                </Button>
              </div>

              {invokeResult && (
                <div className="space-y-1.5">
                  <div className="flex items-center gap-2 text-xs">
                    {invokeResult.ok ? (
                      <Check className="h-3.5 w-3.5 text-emerald-700 dark:text-emerald-400" />
                    ) : (
                      <X className="h-3.5 w-3.5 text-red-700 dark:text-red-400" />
                    )}
                    <span className="text-muted-foreground">
                      Engine RPC {invokeResult.ok ? "succeeded" : "failed"}
                    </span>
                  </div>
                  <pre className="max-h-96 overflow-auto rounded border bg-muted/40 p-3 font-mono text-[11px] leading-relaxed">
                    {formatJson(invokeResult)}
                  </pre>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Panel 4 — Broadcast plumb -------------------------------------- */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-sm">
                <Radio className="h-4 w-4 text-amber-700 dark:text-amber-400" />
                Broadcast plumb
                {broadcastStatus?.enabled ? (
                  <Badge variant="default" className="text-[10px]">
                    enabled
                  </Badge>
                ) : (
                  <Badge variant="secondary" className="text-[10px]">
                    disabled
                  </Badge>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-xs">
              <div className="grid gap-2 md:grid-cols-2">
                <div>
                  <span className="text-muted-foreground">Setting:</span>{" "}
                  <code className="rounded bg-muted px-1.5 py-0.5 font-mono">
                    {broadcastStatus?.setting_key ??
                      "extension_broadcast_enabled"}
                  </code>
                </div>
                <div>
                  <span className="text-muted-foreground">Channel:</span>{" "}
                  <code className="rounded bg-muted px-1.5 py-0.5 font-mono">
                    {user?.id
                      ? `matrx-local-bridge:${user.id}`
                      : (broadcastStatus?.channel_template ??
                        "matrx-local-bridge:<user_id>")}
                  </code>
                </div>
              </div>

              {!broadcastStatus?.enabled && (
                <div className="rounded border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-amber-700 dark:text-amber-300">
                  Broadcast plumb is OFF. Toggle "Enable Broadcast plumb" under
                  Settings → Remote Access → Extension Bridge to enable
                  cross-machine fallback.
                </div>
              )}

              {broadcastError && (
                <div
                  className="rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-red-700 dark:text-red-300"
                  role="alert"
                >
                  Broadcast test failed: {broadcastError}
                </div>
              )}

              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={
                    !isEngineReady ||
                    !broadcastStatus?.enabled ||
                    broadcastBusy ||
                    !user?.id
                  }
                  onClick={sendBroadcastTest}
                >
                  {broadcastBusy ? (
                    <RefreshCw className="mr-2 h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Send className="mr-2 h-3.5 w-3.5" />
                  )}
                  Test publish
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={!isEngineReady}
                  onClick={refreshBroadcastStatus}
                >
                  <RefreshCw className="mr-2 h-3.5 w-3.5" />
                  Refresh status
                </Button>
              </div>

              {broadcastResult && (
                <div className="rounded border bg-muted/40 px-3 py-2">
                  <div className="flex items-center gap-2">
                    {broadcastResult.sent ? (
                      <Check className="h-3.5 w-3.5 text-emerald-700 dark:text-emerald-400" />
                    ) : (
                      <X className="h-3.5 w-3.5 text-amber-700 dark:text-amber-400" />
                    )}
                    <span>
                      {broadcastResult.sent
                        ? "Publish sent successfully"
                        : broadcastResult.enabled
                          ? "Publish skipped — user channel not connected"
                          : "Publish skipped — feature flag is off"}
                    </span>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Panel 5 — Live event log --------------------------------------- */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-sm">
                <ActivityIcon className="h-4 w-4 text-cyan-700 dark:text-cyan-400" />
                Live event log
                {logConnected ? (
                  <Badge variant="default" className="text-[10px]">
                    streaming
                  </Badge>
                ) : (
                  <Badge variant="secondary" className="text-[10px]">
                    offline
                  </Badge>
                )}
                <Badge variant="outline" className="ml-1 text-[10px]">
                  {logEvents.length} / {MAX_LOG_LINES}
                </Badge>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <div className="flex items-center justify-between">
                <div className="text-[11px] text-muted-foreground">
                  RPC, WebSocket, invoke, and broadcast events. Newest at the
                  bottom.
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setLogPaused((p) => !p)}
                  >
                    {logPaused ? (
                      <>
                        <Play className="mr-1.5 h-3 w-3" />
                        Resume
                      </>
                    ) : (
                      <>
                        <Pause className="mr-1.5 h-3 w-3" />
                        Pause
                      </>
                    )}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={clearLog}>
                    <Trash2 className="mr-1.5 h-3 w-3" />
                    Clear
                  </Button>
                </div>
              </div>

              <Separator />

              <div className="dark h-72 overflow-auto rounded border border-zinc-700/50 bg-zinc-950/80 p-2 font-mono text-[11px] text-zinc-200">
                {logEvents.length === 0 ? (
                  <div className="px-2 py-6 text-center text-muted-foreground">
                    No events yet. Trigger an action above to see live bridge
                    traffic.
                  </div>
                ) : (
                  logEvents.map((evt, idx) => (
                    <div
                      key={`${evt.timestamp}-${idx}`}
                      className={cn(
                        "flex items-start gap-2 border-b border-white/5 px-1 py-0.5",
                      )}
                    >
                      <span className="shrink-0 text-zinc-500">
                        {new Date(evt.timestamp).toLocaleTimeString()}
                      </span>
                      <span
                        className={cn(
                          "shrink-0 w-12 text-center uppercase",
                          levelClassFor(evt.direction),
                        )}
                      >
                        {evt.direction}
                      </span>
                      <span className="shrink-0 w-44 truncate text-zinc-300">
                        {evt.kind}
                      </span>
                      <span
                        className="flex-1 truncate text-zinc-400"
                        title={formatJson(evt.payload)}
                      >
                        {formatJson(evt.payload)}
                      </span>
                    </div>
                  ))
                )}
              </div>
            </CardContent>
          </Card>
        </div>
      </ScrollArea>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components — kept private to this file; not reused elsewhere.
// ---------------------------------------------------------------------------

function RpcResultCard({
  label,
  result,
}: {
  label: string;
  result: ExtensionRpcResponse | null;
}) {
  return (
    <div className="rounded border bg-muted/30 p-2 text-xs">
      <div className="mb-1 flex items-center gap-1.5 text-muted-foreground">
        <span className="font-medium">{label}</span>
        {result &&
          (result.ok ? (
            <Check className="h-3 w-3 text-emerald-700 dark:text-emerald-400" />
          ) : (
            <X className="h-3 w-3 text-red-700 dark:text-red-400" />
          ))}
      </div>
      {result ? (
        <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words font-mono text-[10px]">
          {formatJson(result.data ?? result.error ?? result)}
        </pre>
      ) : (
        <div className="text-muted-foreground">Not run yet.</div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pairing — sub-section inside Panel 1.
//
// Shows the engine-issued pairing token. Same-machine extensions auto-pair
// over loopback without ever seeing this screen; the visible code exists so
// the user can manually pair an extension running on ANOTHER machine (over
// the tunnel, where `/extension/pair` is hard-rejected by design). Hidden
// behind a reveal toggle so a screen-share doesn't leak it.
// ---------------------------------------------------------------------------

function PairingSection({ isEngineReady }: { isEngineReady: boolean }) {
  const [pairToken, setPairToken] = useState<string | null>(null);
  const [pairError, setPairError] = useState<string | null>(null);
  const [revealed, setRevealed] = useState(false);
  const [copied, setCopied] = useState(false);

  const fetchPairInfo = useCallback(async () => {
    try {
      const info = await engine.extensionGetPairInfo();
      setPairToken(info.pair_token);
      setPairError(null);
    } catch (e) {
      setPairError(String(e));
    }
  }, []);

  useEffect(() => {
    if (!isEngineReady) return;
    void fetchPairInfo();
  }, [isEngineReady, fetchPairInfo]);

  const copyToken = useCallback(async () => {
    if (!pairToken) return;
    await navigator.clipboard.writeText(pairToken);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }, [pairToken]);

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-xs font-medium">
        <ShieldCheck className="h-3.5 w-3.5 text-sky-700 dark:text-sky-400" />
        Extension pairing
      </div>
      <p className="text-xs text-muted-foreground">
        Extensions on this computer pair automatically. To pair a browser on
        another machine, copy this code into the extension&apos;s Settings →
        Desktop Bridge → Pair code.
      </p>
      {pairError ? (
        <p className="text-xs text-destructive">{pairError}</p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <code className="rounded bg-muted px-2 py-1 font-mono text-xs">
            {pairToken === null
              ? "(unavailable)"
              : revealed
                ? pairToken
                : `${pairToken.slice(0, 12)}…`}
          </code>
          <Button
            size="sm"
            variant="outline"
            disabled={pairToken === null}
            onClick={() => setRevealed((r) => !r)}
          >
            {revealed ? "Hide" : "Reveal"}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={pairToken === null}
            onClick={copyToken}
          >
            {copied ? (
              <Check className="mr-2 h-3.5 w-3.5 text-emerald-700 dark:text-emerald-400" />
            ) : null}
            {copied ? "Copied" : "Copy"}
          </Button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Metrics — sub-section inside Panel 1.
//
// Polls `/extension/metrics` every 2s while the page is visible (the
// surrounding effect handles visibility gating). Renders a compact
// table: command | total | errors | p50 ms | p95 ms | last called |
// last error. Percentiles are computed client-side from the
// `last_n_latencies_ms` deque snapshot — keeps the engine honest as the
// single source of truth.
// ---------------------------------------------------------------------------

function percentileFromSamples(samples: number[], p: number): number | null {
  if (!samples.length) return null;
  // Simple nearest-rank — adequate for a debug surface, doesn't need
  // interpolation. p in [0, 100].
  const sorted = [...samples].sort((a, b) => a - b);
  const idx = Math.min(
    sorted.length - 1,
    Math.max(0, Math.ceil((p / 100) * sorted.length) - 1),
  );
  return sorted[idx] ?? null;
}

function formatLatencyMs(value: number | null): string {
  if (value === null) return "—";
  if (value < 1) return `${value.toFixed(2)} ms`;
  if (value < 10) return `${value.toFixed(1)} ms`;
  return `${Math.round(value)} ms`;
}

function formatLastCalled(unixMs: number): string {
  if (!unixMs) return "—";
  const ageSec = Math.max(0, Math.round((Date.now() - unixMs) / 1000));
  if (ageSec < 60) return `${ageSec}s ago`;
  if (ageSec < 3600) return `${Math.floor(ageSec / 60)}m ago`;
  return `${Math.floor(ageSec / 3600)}h ago`;
}

interface MetricsSectionProps {
  metrics: ExtensionMetricsSnapshot;
  error: string | null;
  busy: boolean;
  isEngineReady: boolean;
  onRefresh: () => void | Promise<void>;
  onReset: () => void | Promise<void>;
}

function MetricsSection({
  metrics,
  error,
  busy,
  isEngineReady,
  onRefresh,
  onReset,
}: MetricsSectionProps) {
  const overflow =
    "_overflow" in metrics
      ? (metrics["_overflow"] as ExtensionCommandMetrics)
      : null;

  // Sort by total request count descending so the noisiest commands
  // surface at the top. Skip the synthetic _overflow row — it gets its
  // own warning banner.
  const rows = useMemo(() => {
    const entries = Object.entries(metrics).filter(([k]) => k !== "_overflow");
    return entries
      .map(([command, m]) => {
        const samples = Array.isArray(m.last_n_latencies_ms)
          ? m.last_n_latencies_ms
          : [];
        const p50 = percentileFromSamples(samples, 50);
        const p95 = percentileFromSamples(samples, 95);
        return {
          command,
          count: m.count,
          errorCount: m.error_count,
          p50,
          p95,
          lastCalledAt: m.last_called_at,
          lastError: m.last_error,
          sampleSize: samples.length,
        };
      })
      .sort((a, b) => b.count - a.count);
  }, [metrics]);

  const totalRequests = useMemo(
    () => rows.reduce((acc, r) => acc + r.count, 0),
    [rows],
  );
  const totalErrors = useMemo(
    () => rows.reduce((acc, r) => acc + r.errorCount, 0),
    [rows],
  );

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm">
          <Gauge className="h-4 w-4 text-amber-700 dark:text-amber-300" />
          <span className="font-medium">Request metrics</span>
          <Badge variant="secondary" className="text-[10px]">
            {rows.length} commands
          </Badge>
          <Badge variant="outline" className="text-[10px]">
            {totalRequests} total
          </Badge>
          {totalErrors > 0 && (
            <Badge variant="destructive" className="text-[10px]">
              {totalErrors} errors
            </Badge>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="ghost"
            disabled={!isEngineReady}
            onClick={() => void onRefresh()}
          >
            <RefreshCw className="mr-2 h-3.5 w-3.5" />
            Refresh
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={!isEngineReady || busy || rows.length === 0}
            onClick={() => void onReset()}
          >
            {busy ? (
              <RefreshCw className="mr-2 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Trash2 className="mr-2 h-3.5 w-3.5" />
            )}
            Reset
          </Button>
        </div>
      </div>

      <p className="text-[11px] text-muted-foreground">
        In-memory only — resets on engine restart. Latencies sampled from the
        last 100 calls per command. Polls every 2s while this page is visible.
      </p>

      {error && (
        <div className="flex items-center gap-2 rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-700 dark:text-red-300">
          <AlertCircle className="h-3.5 w-3.5" />
          {error}
        </div>
      )}

      {overflow && (
        <div className="flex items-center gap-2 rounded border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
          <AlertCircle className="h-3.5 w-3.5" />
          Distinct-command cap reached. Newer command names are being dropped
          from metrics ({overflow.count} skipped). Reset to recover.
        </div>
      )}

      {rows.length === 0 ? (
        <div className="rounded border border-dashed border-muted-foreground/30 px-3 py-6 text-center text-xs text-muted-foreground">
          No metrics yet. Drive the bridge above (Health, Capabilities, Invoke)
          to populate this table.
        </div>
      ) : (
        <div className="overflow-hidden rounded border">
          <table className="w-full text-xs">
            <thead className="bg-muted/50 text-muted-foreground">
              <tr>
                <th className="px-3 py-2 text-left font-medium">Command</th>
                <th className="px-3 py-2 text-right font-medium">Total</th>
                <th className="px-3 py-2 text-right font-medium">Errors</th>
                <th className="px-3 py-2 text-right font-medium">p50</th>
                <th className="px-3 py-2 text-right font-medium">p95</th>
                <th className="px-3 py-2 text-right font-medium">
                  Last called
                </th>
                <th className="px-3 py-2 text-left font-medium">Last error</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.command} className="border-t hover:bg-muted/30">
                  <td className="px-3 py-2 font-mono" title={r.command}>
                    {r.command}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {r.count}
                  </td>
                  <td
                    className={cn(
                      "px-3 py-2 text-right tabular-nums",
                      r.errorCount > 0 && "text-red-700 dark:text-red-400",
                    )}
                  >
                    {r.errorCount}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                    {formatLatencyMs(r.p50)}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                    {formatLatencyMs(r.p95)}
                  </td>
                  <td className="px-3 py-2 text-right text-muted-foreground">
                    {formatLastCalled(r.lastCalledAt)}
                  </td>
                  <td
                    className="px-3 py-2 max-w-xs truncate text-muted-foreground"
                    title={r.lastError ?? ""}
                  >
                    {r.lastError ?? ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function CapabilitiesCard({
  result,
  count,
  names,
}: {
  result: ExtensionRpcResponse | null;
  count: number | null;
  names: string[];
}) {
  if (!result) {
    return (
      <div className="rounded border bg-muted/30 p-2 text-xs">
        <div className="mb-1 font-medium text-muted-foreground">
          Capabilities
        </div>
        <div className="text-muted-foreground">Not run yet.</div>
      </div>
    );
  }
  if (!result.ok) {
    return (
      <div className="rounded border bg-muted/30 p-2 text-xs">
        <div className="mb-1 flex items-center gap-1.5">
          <span className="font-medium text-muted-foreground">
            Capabilities
          </span>
          <X className="h-3 w-3 text-red-700 dark:text-red-400" />
        </div>
        <pre className="overflow-auto font-mono text-[10px]">
          {formatJson(result.error ?? result)}
        </pre>
      </div>
    );
  }
  return (
    <div className="rounded border bg-muted/30 p-2 text-xs">
      <div className="mb-1 flex items-center gap-1.5">
        <span className="font-medium text-muted-foreground">Capabilities</span>
        <Check className="h-3 w-3 text-emerald-700 dark:text-emerald-400" />
        <Badge variant="outline" className="ml-auto text-[10px]">
          {count ?? "?"} tools
        </Badge>
      </div>
      <ScrollArea className="h-40 pr-2">
        <ul className="space-y-0.5 font-mono text-[10px] text-foreground/80">
          {names.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      </ScrollArea>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tunnel status — sub-section inside Panel 1.
//
// Surfaces the runtime state from `GET /extension/tunnel/status`: whether
// the Cloudflare tunnel is up, the active local + tunnel URLs, the
// engine's preferred-mode hint, and a warning when the engine prefers
// tunnel but the extension may not have refreshed its discovery file
// yet. Manual refresh only — tunnel state changes are infrequent
// enough that polling adds noise without value.
// ---------------------------------------------------------------------------

interface TunnelStatusSectionProps {
  status: ExtensionTunnelStatus | null;
  error: string | null;
  busy: boolean;
  isEngineReady: boolean;
  onRefresh: () => void | Promise<void>;
}

function TunnelStatusSection({
  status,
  error,
  busy,
  isEngineReady,
  onRefresh,
}: TunnelStatusSectionProps) {
  const showRepairHint =
    status !== null && status.preferred === "tunnel" && status.active;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
          <Cloud className="h-3.5 w-3.5 text-violet-700 dark:text-violet-400" />
          Tunnel status
          {status &&
            (status.active ? (
              <Badge variant="outline" className="text-[10px]">
                Active
              </Badge>
            ) : (
              <Badge variant="secondary" className="text-[10px]">
                Inactive
              </Badge>
            ))}
        </div>
        <Button
          size="sm"
          variant="ghost"
          disabled={!isEngineReady || busy}
          onClick={onRefresh}
        >
          <RefreshCw
            className={cn("mr-2 h-3.5 w-3.5", busy && "animate-spin")}
          />
          Refresh
        </Button>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-700 dark:text-red-300">
          <AlertCircle className="h-3.5 w-3.5" />
          {error}
        </div>
      )}

      {status === null ? (
        <div className="rounded border border-dashed border-muted-foreground/30 px-3 py-4 text-center text-xs text-muted-foreground">
          {isEngineReady
            ? "Tunnel state has not been fetched yet."
            : "Engine not connected — tunnel state unavailable."}
        </div>
      ) : (
        <div className="grid gap-2 text-xs sm:grid-cols-2">
          <div className="rounded border bg-muted/30 p-2">
            <div className="mb-1 text-muted-foreground">Local URL</div>
            <code className="block break-all font-mono text-[11px]">
              {status.local_url}
            </code>
            <div className="mt-1 text-[10px] text-muted-foreground">
              ws: <code className="font-mono">{status.local_ws}</code>
            </div>
          </div>

          <div className="rounded border bg-muted/30 p-2">
            <div className="mb-1 flex items-center justify-between text-muted-foreground">
              <span>Tunnel URL</span>
              <Badge variant="outline" className="text-[10px]">
                {status.mode}
              </Badge>
            </div>
            {status.tunnel_url ? (
              <>
                <code className="block break-all font-mono text-[11px]">
                  {status.tunnel_url}
                </code>
                <div className="mt-1 text-[10px] text-muted-foreground">
                  ws: <code className="font-mono">{status.tunnel_ws}</code>
                </div>
                <div className="mt-1 text-[10px] text-muted-foreground">
                  uptime: {Math.round(status.uptime_seconds)}s
                </div>
              </>
            ) : (
              <div className="text-muted-foreground">No active tunnel.</div>
            )}
          </div>

          <div className="rounded border bg-muted/30 p-2 sm:col-span-2">
            <div className="mb-1 flex items-center gap-1.5 text-muted-foreground">
              <span>Preferred mode</span>
              <Badge
                variant={
                  status.preferred === "tunnel" ? "default" : "secondary"
                }
                className="text-[10px]"
              >
                {status.preferred}
              </Badge>
              {status.prefer_tunnel && (
                <span className="text-[10px] text-muted-foreground">
                  (MATRX_PREFER_TUNNEL=true)
                </span>
              )}
            </div>
            {showRepairHint ? (
              <div className="mt-1 flex items-start gap-1.5 text-[11px] text-amber-700 dark:text-amber-300">
                <AlertCircle className="mt-0.5 h-3 w-3 flex-shrink-0" />
                <span>
                  Engine prefers tunnel — the extension may need to be re-paired
                  to pick up the latest tunnel URL.
                </span>
              </div>
            ) : (
              <div className="text-[10px] text-muted-foreground">
                The extension should call this URL when it has a choice.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Boot self-check — sub-section inside Panel 1.
//
// Renders the cached summary of the engine's startup self-check (see
// `app/api/extension_boot_check.py`). Cheap reads via
// `GET /extension/boot-check`; "Re-run self-check" button hits
// `POST /extension/boot-check/run` to refresh both the cache and this
// view at the same time.
//
// One row per check with a coloured status badge and the engine's own
// detail message. The summary's overall ok flag and finished_at
// timestamp render in the section header so the user can see at a
// glance whether the bridge is currently coherent.
// ---------------------------------------------------------------------------

interface BootCheckSectionProps {
  summary: ExtensionBootCheckSummary | null;
  error: string | null;
  busy: boolean;
  isEngineReady: boolean;
  onRerun: () => void | Promise<void>;
}

function bootCheckStatusClass(status: "ok" | "warn" | "fail"): string {
  if (status === "ok") return "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300";
  if (status === "warn") return "bg-amber-500/15 text-amber-700 dark:text-amber-300";
  return "bg-red-500/15 text-red-700 dark:text-red-300";
}

function formatBootCheckTimestamp(unixSec: number): string {
  if (!unixSec) return "—";
  const ageSec = Math.max(0, Math.round(Date.now() / 1000 - unixSec));
  if (ageSec < 60) return `${ageSec}s ago`;
  if (ageSec < 3600) return `${Math.floor(ageSec / 60)}m ago`;
  return `${Math.floor(ageSec / 3600)}h ago`;
}

function BootCheckSection({
  summary,
  error,
  busy,
  isEngineReady,
  onRerun,
}: BootCheckSectionProps) {
  const hasChecks = !!summary && summary.checks.length > 0;
  const overallOk = !!summary?.ok;
  const overallBadgeVariant: "default" | "secondary" | "destructive" =
    !summary || !hasChecks
      ? "secondary"
      : overallOk
        ? "default"
        : "destructive";

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm">
          <ShieldCheck className="h-4 w-4 text-sky-700 dark:text-sky-300" />
          <span className="font-medium">Boot self-check</span>
          {hasChecks && (
            <Badge variant={overallBadgeVariant} className="text-[10px]">
              {overallOk ? "ok" : "fail"}
            </Badge>
          )}
          {hasChecks && (
            <Badge variant="outline" className="text-[10px]">
              {summary!.checks.length} checks
            </Badge>
          )}
          {hasChecks && (
            <span className="text-[10px] text-muted-foreground">
              {summary!.duration_ms.toFixed(0)} ms,{" "}
              {formatBootCheckTimestamp(summary!.finished_at)}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="ghost"
            disabled={!isEngineReady || busy}
            onClick={() => void onRerun()}
          >
            {busy ? (
              <RefreshCw className="mr-2 h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="mr-2 h-3.5 w-3.5" />
            )}
            Re-run self-check
          </Button>
        </div>
      </div>

      <p className="text-[11px] text-muted-foreground">
        Verifies /extension/* routes registered, JWT validation posture,
        tunnel-state singleton, metrics module, and ~/.matrx/local.json. Runs
        once at engine startup; rerun here to refresh after changing config
        without restarting.
      </p>

      {error && (
        <div className="flex items-center gap-2 rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-700 dark:text-red-300">
          <AlertCircle className="h-3.5 w-3.5" />
          {error}
        </div>
      )}

      {!hasChecks ? (
        <div className="rounded border border-dashed border-muted-foreground/30 px-3 py-6 text-center text-xs text-muted-foreground">
          {summary?.message ?? "Boot self-check has not yet run."}
        </div>
      ) : (
        <div className="overflow-hidden rounded border">
          <table className="w-full text-xs">
            <thead className="bg-muted/50 text-muted-foreground">
              <tr>
                <th className="px-3 py-2 text-left font-medium">Check</th>
                <th className="px-3 py-2 text-left font-medium">Status</th>
                <th className="px-3 py-2 text-left font-medium">Detail</th>
                <th className="px-3 py-2 text-right font-medium">Duration</th>
              </tr>
            </thead>
            <tbody>
              {summary!.checks.map((c) => (
                <tr key={c.name} className="border-t hover:bg-muted/30">
                  <td className="px-3 py-2 font-mono">{c.name}</td>
                  <td className="px-3 py-2">
                    <span
                      className={cn(
                        "inline-flex rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide",
                        bootCheckStatusClass(c.status),
                      )}
                    >
                      {c.status}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">
                    {c.message}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {c.duration_ms < 1
                      ? `${c.duration_ms.toFixed(2)} ms`
                      : `${Math.round(c.duration_ms)} ms`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
