import { useState, useEffect, useCallback, useRef } from "react";
import { engine, type SystemInfo, type BrowserStatus } from "@/lib/api";
import { isTauri, startSidecar, stopSidecar, waitForEngine, discoverEnginePort } from "@/lib/sidecar";
import { ENGINE_DEFAULT_URL, ENGINE_PORT_BASE, ENGINE_PORT_RANGE_LABEL } from "@/lib/engine-ports";
import { initPlatformCtx } from "@/lib/platformCtx";
import { startBackgroundTasks, stopBackgroundTasks } from "@/lib/background-tasks";
import supabase from "@/lib/supabase";
import { emitClientLog } from "@/hooks/use-client-log";
import { useWindowLeader } from "@/hooks/use-window-leader";

export type EngineStatus = "discovering" | "starting" | "connected" | "disconnected" | "error";

interface EngineState {
  status: EngineStatus;
  url: string | null;
  tools: string[];
  systemInfo: SystemInfo | null;
  browserStatus: BrowserStatus | null;
  engineVersion: string;
  error: string | null;
  wsConnected: boolean;
}

export function useEngine(authenticated = true) {
  const [state, setState] = useState<EngineState>({
    status: "discovering",
    url: null,
    tools: [],
    systemInfo: null,
    browserStatus: null,
    engineVersion: "",
    error: null,
    wsConnected: false,
  });

  // Leader gating (multi-window): only the leader window runs the singleton
  // services — background-task orchestrator + periodic cloud heartbeat. The
  // per-window engine connection, status, and health poll are NOT gated.
  const isLeader = useWindowLeader();
  const isLeaderRef = useRef(isLeader);
  isLeaderRef.current = isLeader;

  const mountedRef = useRef(true);
  // Prevents concurrent duplicate runs — but does NOT prevent future retries
  // like the old initRef did.
  const initializingRef = useRef(false);
  const statusRef = useRef(state.status);
  statusRef.current = state.status;
  // Mirrors the wsConnected state for use inside closures without stale capture.
  const wsConnectedRef = useRef(state.wsConnected);
  wsConnectedRef.current = state.wsConnected;
  // Timestamp when the engine was first discovered. Used to suppress false
  // "disconnected" flips during the engine's slow startup phase (~60s).
  const connectedAtRef = useRef<number | null>(null);
  // Timestamp of the last successful cloud configure call. Used to deduplicate
  // the configure call that fires from onAuthStateChange(INITIAL_SESSION) when
  // initialize() has already done it within the last 10 seconds.
  const lastCloudConfigureRef = useRef<number>(0);

  // Wire up the token provider immediately so all authenticated calls have
  // access to the current JWT. This must happen before initialize() runs.
  // We always register it — the provider handles the case where no session
  // exists by returning null, which authHeaders() converts to no header.
  useEffect(() => {
    engine.setTokenProvider(async () => {
      const { data: { session } } = await supabase.auth.getSession();
      return session?.access_token ?? null;
    });
  }, []);

  const update = useCallback((partial: Partial<EngineState>) => {
    if (mountedRef.current) {
      setState((prev) => {
        if (partial.status && partial.status !== prev.status) {
          emitClientLog("info", `Engine status: ${prev.status} → ${partial.status}`, "engine");
        }
        if (partial.error && partial.error !== prev.error) {
          emitClientLog("error", `Engine error: ${partial.error}`, "engine");
        }
        return { ...prev, ...partial };
      });
    }
  }, []);

  /**
   * Core engine initialization.
   *
   * Key changes from the previous implementation:
   * 1. Uses a "currently running" mutex instead of a one-shot flag —
   *    subsequent calls are allowed once the prior one finishes.
   * 2. After startSidecar(), waits up to 60s for the engine health
   *    endpoint before running port discovery.
   * 3. On failure, sets status to "error" (not "disconnected") so the
   *    recovery modal activates.
   */
  const initialize = useCallback(async () => {
    // Already running — skip this call (will be retried later)
    if (initializingRef.current) return;

    // Engine is already connected — verify it's genuinely alive before
    // deciding to skip. This prevents Supabase token refresh events
    // (SIGNED_IN / TOKEN_REFRESHED on visibility return) from bouncing
    // the status to "discovering" and flashing the StartupScreen.
    // Works identically on macOS, Windows, and Linux.
    if (statusRef.current === "connected") {
      const alive = await engine.isHealthy();
      if (alive) return;
    }

    initializingRef.current = true;

    try {
      update({ status: "discovering", error: null });
      emitClientLog("cmd", "Engine initialization started", "engine");

      // Holds the engine URL confirmed via Rust IPC (bypasses Windows loopback restriction).
      // Set inside the Tauri block below; used to skip the JS fetch port scan.
      let tauriConfirmedUrl: string | null = null;

      // In Tauri, start the sidecar first
      if (isTauri()) {
        update({ status: "starting" });
        emitClientLog("info", "Starting sidecar process...", "engine");
        try {
          await startSidecar();
          emitClientLog("success", "Sidecar process spawned", "engine");
        } catch (err) {
          emitClientLog("error", `startSidecar failed: ${err}`, "engine");
          update({
            status: "error",
            error: `Failed to start engine: ${err}`,
          });
          return;
        }

        // ── Wait for the sidecar to become reachable ─────────────────
        // startSidecar() returns as soon as the process is spawned.
        // The PyInstaller binary takes 5-30s to boot and bind a port.
        // We poll via Rust IPC (check_engine_health) which bypasses
        // Windows WebView2 loopback isolation that blocks JS fetch().
        //
        // Every 5 seconds we emit a heartbeat log so the UI shows progress
        // instead of 60 seconds of silence.
        emitClientLog("info", "Waiting for engine to become reachable (up to 60s)...", "engine");

        // Heartbeat ticker — fires every 5s while we wait
        let heartbeatSeconds = 0;
        const heartbeatInterval = setInterval(() => {
          heartbeatSeconds += 5;
          emitClientLog("info", `Still starting up... (${heartbeatSeconds}s elapsed)`, "engine");
        }, 5000);

        let ready = false;
        try {
          ready = await waitForEngine(ENGINE_DEFAULT_URL, 60, 1000);
        } finally {
          clearInterval(heartbeatInterval);
        }

        let confirmedUrl: string | null = ready ? ENGINE_DEFAULT_URL : null;
        if (!ready) {
          emitClientLog("warn", "Default port not responding — scanning port range...", "engine");
          confirmedUrl = await discoverEnginePort();
          if (!confirmedUrl) {
            // ── Full diagnostic dump on failure ──────────────────────────────
            emitClientLog("error", "Engine never became reachable — sidecar may have crashed", "engine");
            try {
              // Fetch buffered sidecar logs to include in the error report
              const { getSidecarLogs: getLogs } = await import("@/lib/sidecar");
              const recentLogs = await getLogs();
              const tail = recentLogs.slice(-30);
              if (tail.length > 0) {
                emitClientLog("error", "=== Last 30 engine output lines ===", "engine");
                tail.forEach((line) => emitClientLog("error", `  ${line}`, "engine"));
                emitClientLog("error", "=== End of engine output ===", "engine");
              }
            } catch { /* ignore — best effort */ }
            update({
              status: "error",
              error: "Engine process started but never became reachable. The sidecar may have crashed during startup. Open the Engine Monitor for detailed logs.",
            });
            return;
          }
          emitClientLog("success", `Engine found at alternate port: ${confirmedUrl}`, "engine");
        } else {
          emitClientLog("success", `Engine is responding on port ${ENGINE_PORT_BASE}`, "engine");
        }

        // Pass the confirmed URL directly so engine.discover() doesn't do another
        // round of JS fetch() scans (which are blocked on Windows by WebView2).
        tauriConfirmedUrl = confirmedUrl;
      }

      // Discover the engine URL. In Tauri we pass the Rust-confirmed URL to skip
      // JS fetch() port scanning (blocked on Windows by WebView2 loopback isolation).
      // In browser dev mode, fall through to the standard JS port scan.
      emitClientLog("info", "Discovering engine URL...", "engine");
      const url = tauriConfirmedUrl
        ? await engine.discover(tauriConfirmedUrl)
        : await engine.discover();
      if (!url) {
        emitClientLog("error", `Engine not found on any port ${ENGINE_PORT_RANGE_LABEL}`, "engine");
        update({
          status: "error",
          error: `Engine not found on any port (${ENGINE_PORT_RANGE_LABEL}). Make sure the Python server is running.`,
        });
        return;
      }

      emitClientLog("success", `Engine discovered at ${url}`, "engine");
      connectedAtRef.current = Date.now();
      update({ url, status: "connected", error: null });

      // Populate the frontend platform context from the engine
      try {
        const ctx = await engine.getPlatformContext();
        initPlatformCtx(ctx);
        emitClientLog("info", "Platform context initialised from engine", "engine");
      } catch {
        emitClientLog("warn", "Could not load platform context (browser fallback active)", "engine");
      }

      // Load tools list
      try {
        const tools = await engine.listTools();
        update({ tools });
        emitClientLog("info", `Loaded ${tools.length} tools`, "engine");
      } catch {
        emitClientLog("warn", "Could not load tools list (non-critical)", "engine");
      }

      // Load engine version
      try {
        const engineVersion = await engine.getVersion();
        update({ engineVersion });
        emitClientLog("info", `Engine version: ${engineVersion}`, "engine");
      } catch { /* non-critical */ }

      // Load system info
      try {
        const systemInfo = await engine.getSystemInfo();
        update({ systemInfo });
        emitClientLog("info", `System: ${systemInfo?.platform ?? "?"} / ${systemInfo?.hostname ?? "?"}`, "engine");
      } catch {
        emitClientLog("warn", "Could not load system info (non-critical)", "engine");
      }

      // Load browser status
      try {
        const browserStatus = await engine.getBrowserStatus();
        update({ browserStatus });
        emitClientLog("info", `Browser: ${browserStatus?.chrome_found ? "available" : "not found"}`, "engine");
      } catch {
        emitClientLog("warn", "Could not load browser status (non-critical)", "engine");
      }

      // Connect WebSocket — only when we have a token; the server rejects
      // unauthenticated WS connections with 403 and the auto-reconnect loop
      // would hammer the server until auth is available.
      try {
        const { data: { session } } = await supabase.auth.getSession();
        if (session?.access_token) {
          await engine.connectWebSocket();
          update({ wsConnected: true });
          emitClientLog("success", "WebSocket connected", "engine");
        } else {
          emitClientLog("warn", "No session token — skipping WebSocket (REST still works)", "engine");
        }
      } catch (err) {
        emitClientLog("warn", `WebSocket connection failed (non-critical): ${err}`, "engine");
      }

      // Fire background tasks: token sync, cloud configure, settings hydration,
      // prefetch, and heartbeat all run via the idle-scheduled orchestrator.
      // Leader-only: with multiple windows, exactly one runs the orchestrator
      // (a late promotion is handled by the isLeader effect below).
      lastCloudConfigureRef.current = Date.now();
      if (isLeaderRef.current) {
        startBackgroundTasks();
        emitClientLog("success", "Engine initialization complete — background tasks queued", "engine");
      } else {
        emitClientLog("success", "Engine initialization complete (non-leader window — background tasks skipped)", "engine");
      }
    } finally {
      // Always release the mutex so future retries are possible
      initializingRef.current = false;
    }
  }, [update]);

  /**
   * Full restart: stop → start → wait → discover → init.
   * This is what all "Restart Engine" buttons should call.
   */
  const restartEngine = useCallback(async () => {
    // Don't restart if already initializing — but allow if "error" or "disconnected"
    if (initializingRef.current) return;

    update({ status: "starting", error: null });

    if (isTauri()) {
      try {
        await stopSidecar();
      } catch {
        // May already be stopped — that's fine
      }
      // Small delay to let the OS release the port
      await new Promise((r) => setTimeout(r, 500));
    }

    // Re-run initialization which handles start + wait + discover
    await initialize();
  }, [initialize, update]);

  /**
   * Reconnect / refresh: re-run the full initialization sequence.
   * Unlike the old version, this always re-runs (no one-shot gate).
   */
  const refresh = useCallback(async () => {
    await initialize();
  }, [initialize]);

  // Leader promotion: if this window becomes leader AFTER its initialize()
  // already completed (the previous leader window closed), the orchestrator
  // was skipped back then — start it now. orchestrator.start() is idempotent,
  // and demotion never happens without window destruction, so cleanup here is
  // belt-and-suspenders only.
  useEffect(() => {
    if (!isLeader) return;
    if (statusRef.current === "connected") {
      emitClientLog("info", "Promoted to leader window — starting background tasks", "engine");
      startBackgroundTasks();
    }
    return () => stopBackgroundTasks();
  }, [isLeader]);

  // Trigger initialize() whenever authentication state first becomes true.
  // Guard against re-running when already connected — Supabase fires SIGNED_IN
  // on every token refresh (including on window refocus), which would cause the
  // engine to restart any time the user switches away from the app.
  useEffect(() => {
    if (authenticated && statusRef.current !== "connected") {
      initialize();
    }
  }, [authenticated, initialize]);

  // Belt-and-suspenders: also watch supabase auth state directly so we catch
  // the SIGNED_IN event that fires from setSession() in completeOAuthExchange,
  // in case the authenticated prop hasn't propagated yet when it fires.
  //
  // Supabase-js fires SIGNED_IN on visibility return (tab/window refocus) when
  // it refreshes an expiring token. If the engine is already connected we must
  // NOT call initialize() — that would reset status to "discovering" and flash
  // the StartupScreen. Instead, only patch up the WebSocket if it dropped.
  // This behaviour is identical on macOS, Windows, and Linux.
  useEffect(() => {
    const { data: { subscription } } = supabase.auth.onAuthStateChange((event, session) => {
      if (event === "SIGNED_IN") {
        if (statusRef.current === "connected") {
          if (!wsConnectedRef.current && session?.access_token) {
            // Supabase holds its auth lock while this callback runs.
            // connectWebSocket() obtains the token through getSession(), so
            // awaiting it here deadlocks that lock and every later authenticated
            // engine request (including image-model loads).  Leave the callback
            // first, then reconnect once Supabase has released the lock.
            setTimeout(() => {
              void engine.connectWebSocket().then(() => {
                update({ wsConnected: true });
                emitClientLog("success", "WebSocket connected (deferred — auth arrived after engine)", "engine");
              }).catch((err) => {
                emitClientLog("warn", `Deferred WebSocket connection failed: ${err}`, "engine");
              });
            }, 0);
          }
        } else {
          setTimeout(() => void initialize(), 0);
        }
      }
    });
    return () => subscription.unsubscribe();
  }, [initialize, update]);

  useEffect(() => {
    mountedRef.current = true;

    const offConnected = engine.on("connected", () =>
      update({ wsConnected: true })
    );
    const offDisconnected = engine.on("disconnected", () =>
      update({ wsConnected: false })
    );

    // Re-configure cloud sync and sync JWT to Python whenever auth state changes.
    const { data: { subscription: authSub } } = supabase.auth.onAuthStateChange(
      (event, session) => {
        if (!engine.engineUrl) return;
        // Never keep Supabase's auth lock while doing network work. Besides
        // blocking future auth reads, some engine calls obtain the current
        // token through getSession() and would self-deadlock inside this callback.
        setTimeout(async () => {
          if (event === "SIGNED_IN" || event === "INITIAL_SESSION") {
            if (session?.access_token && session?.user?.id) {
              // Push the JWT to Python so it persists across restarts.
              engine.syncTokenToPython(
                session.access_token,
                session.user.id,
                session.refresh_token ?? undefined,
                session.expires_in ?? undefined,
              ).catch((e) => console.warn("[engine] syncTokenToPython failed:", e));

              // Skip if initialize() already sent configure within the last 10s
              // to avoid a duplicate call on the INITIAL_SESSION event.
              if (Date.now() - lastCloudConfigureRef.current < 10_000) return;
              try {
                lastCloudConfigureRef.current = Date.now();
                await engine.configureCloudSync(session.access_token, session.user.id);
                engine.cloudHeartbeat().catch((e) => console.warn("[engine] cloudHeartbeat failed:", e));
              } catch (e) {
                console.warn("[engine] configureCloudSync failed (non-critical):", e);
              }
            }
          } else if (event === "TOKEN_REFRESHED") {
            if (session?.access_token && session?.user?.id) {
              // Push refreshed JWT to Python immediately.
              engine.syncTokenToPython(
                session.access_token,
                session.user.id,
                session.refresh_token ?? undefined,
                session.expires_in ?? undefined,
              ).catch((e) => console.warn("[engine] syncTokenToPython (refresh) failed:", e));
              try {
                await engine.reconfigureCloudSync(session.access_token, session.user.id);
              } catch (e) {
                console.warn("[engine] reconfigureCloudSync failed (non-critical):", e);
              }
            }
          } else if (event === "SIGNED_OUT") {
            engine.clearPythonToken().catch((e) => console.warn("[engine] clearPythonToken failed:", e));
          }
        }, 0);
      }
    );

    // Periodic health check — runs every 10s, but suppresses false "disconnected"
    // flips for 90s after first connection to allow for slow engine startup.
    const STARTUP_GRACE_MS = 90_000;
    const healthInterval = setInterval(async () => {
      const healthy = await engine.isHealthy();
      if (!healthy && statusRef.current === "connected") {
        const msSinceConnect = connectedAtRef.current
          ? Date.now() - connectedAtRef.current
          : Infinity;
        if (msSinceConnect > STARTUP_GRACE_MS) {
          update({ status: "disconnected" });
        }
        // Within grace period: engine is still booting, stay "connected"
      } else if (
        healthy &&
        (statusRef.current === "disconnected" || statusRef.current === "error")
      ) {
        // isHealthy() self-heals a null/stale base URL via re-discovery, so by
        // the time it returns true, engine.engineUrl points at the live engine.
        // Propagate that URL into state (it may have changed ports) and lift the
        // status back to "connected" — this is what recovers a permanently
        // null-locked engine URL after a flap, WITHOUT a manual restart.
        connectedAtRef.current = Date.now();
        update({ status: "connected", url: engine.engineUrl, error: null });
      }
    }, 10000);

    // Periodic cloud heartbeat (every 5 minutes) — leader window only; the
    // engine owns the device identity, so N windows heartbeating is pure
    // redundancy. Checked per-tick via ref so promotion needs no re-mount.
    const heartbeatInterval = setInterval(() => {
      if (!isLeaderRef.current) return;
      engine.cloudHeartbeat().catch((e) => console.warn("[engine] periodic heartbeat failed:", e));
    }, 300000);

    return () => {
      mountedRef.current = false;
      offConnected();
      offDisconnected();
      authSub.unsubscribe();
      clearInterval(healthInterval);
      clearInterval(heartbeatInterval);
      stopBackgroundTasks();
      engine.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { ...state, refresh, restartEngine, engine };
}
