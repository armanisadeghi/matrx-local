import { useState, useEffect, useCallback, useRef } from "react";
import { engine, type SystemInfo } from "@/lib/api";
import {
  ENGINE_STARTUP_TIMEOUT_SECONDS,
  isTauri,
  startSidecar,
  stopSidecar,
  waitForOwnedEngine,
} from "@/lib/sidecar";
import { ENGINE_PORT_RANGE_LABEL } from "@/lib/engine-ports";
import { initPlatformCtx } from "@/lib/platformCtx";
import { startBackgroundTasks, stopBackgroundTasks } from "@/lib/background-tasks";
import { getAuthedSession, getToken } from "@/lib/custodian";
import { emitClientLog } from "@/hooks/use-client-log";
import { useWindowLeader } from "@/hooks/use-window-leader";
import {
  nativeVaultEngineTransitionContext,
  alignNativeVaultEngineForCurrentSubject,
  subscribeNativeVaultHostEvents,
} from "@/lib/native-vault-auth";

export type EngineStatus = "discovering" | "starting" | "connected" | "disconnected" | "error";

interface EngineState {
  status: EngineStatus;
  url: string | null;
  tools: string[];
  systemInfo: SystemInfo | null;
  engineVersion: string;
  error: string | null;
  wsConnected: boolean;
}

export function useEngine() {
  const [state, setState] = useState<EngineState>({
    status: "discovering",
    url: null,
    tools: [],
    systemInfo: null,
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
      const session = await getAuthedSession();
      return session?.access_token && nativeVaultEngineTransitionContext(session.user?.id)
        ? session.access_token
        : null;
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
   * 2. After startSidecar(), follows the Rust-owned process and its actual
   *    port through the full packaged cold-start window.
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
        // A packaged cold start can take over a minute to boot and bind a port.
        // We poll via Rust IPC (check_engine_health) which bypasses
        // Windows WebView2 loopback isolation that blocks JS fetch().
        //
        // Every 5 seconds we emit a heartbeat log so the UI shows progress
        // instead of leaving a long silent startup window.
        emitClientLog(
          "info",
          `Waiting for engine to become reachable (up to ${ENGINE_STARTUP_TIMEOUT_SECONDS}s)...`,
          "engine",
        );

        // Heartbeat ticker — fires every 5s while we wait
        let heartbeatSeconds = 0;
        const heartbeatInterval = setInterval(() => {
          heartbeatSeconds += 5;
          emitClientLog("info", `Still starting up... (${heartbeatSeconds}s elapsed)`, "engine");
        }, 5000);

        let startupResult;
        try {
          startupResult = await waitForOwnedEngine();
        } finally {
          clearInterval(heartbeatInterval);
        }

        const confirmedUrl = startupResult.url;
        if (!confirmedUrl) {
          // ── Full diagnostic dump on failure ──────────────────────────────
          const failureSummary = startupResult.outcome === "exited"
            ? "Engine process exited before it became reachable"
            : `Engine process remained unreachable for ${ENGINE_STARTUP_TIMEOUT_SECONDS}s`;
          emitClientLog("error", failureSummary, "engine");
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
            error: startupResult.outcome === "exited"
              ? "Engine process exited during startup. Open the Engine Monitor for detailed logs."
              : `Engine process did not become reachable within ${ENGINE_STARTUP_TIMEOUT_SECONDS} seconds. Open the Engine Monitor for detailed logs.`,
          });
          return;
        }
        emitClientLog("success", `Engine is responding at ${confirmedUrl}`, "engine");

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

      // Establish the full engine session before starting any authenticated
      // work.  A WebSocket can authenticate its own handshake, but it does
      // not populate the engine's persisted JWT.  Previously startup treated
      // a successful WebSocket as sufficient and started the idle queue; the
      // first queued token hand-off could then be fenced by an auth event,
      // leaving every REST request from the running desktop unauthenticated.
      //
      // The persisted hand-off is deliberately before the WebSocket: the
      // local API is usable only once both current-account fencing and engine
      // credential custody agree on this exact session.
      let acceptedSessionForTasks = false;
      try {
        const session = await getAuthedSession();
        if (session?.access_token) {
          await alignNativeVaultEngineForCurrentSubject(session.user?.id ?? null);
          if (!nativeVaultEngineTransitionContext(session.user?.id)) {
            throw new Error("Native Vault account fence has not adopted this session");
          }
          const context = nativeVaultEngineTransitionContext(session.user?.id);
          if (!context) throw new Error("Native Vault account fence has not adopted this session");
          // FS-C5b: nothing is pushed to the engine any more. It asks the sync daemon for its own
          // token (app/services/sync_client), so there is no window in which the engine holds a
          // credential this window handed it and nothing headless can renew — MXL-D-046's shape.
          await engine.connectWebSocket(context);
          acceptedSessionForTasks = true;
          update({ wsConnected: true });
          emitClientLog("success", "WebSocket connected", "engine");
        } else {
          emitClientLog("warn", "No session token — skipping WebSocket (REST still works)", "engine");
        }
      } catch (err) {
        emitClientLog("warn", `WebSocket connection failed (non-critical): ${err}`, "engine");
      }

      // The queue reads tokens itself, so it starts only after the exact
      // current subject has passed native reconciliation. A failed/anonymous
      // initialization must leave the queue stopped.
      if (isLeaderRef.current && acceptedSessionForTasks) {
        lastCloudConfigureRef.current = Date.now();
        startBackgroundTasks();
        emitClientLog("success", "Engine initialization complete — background tasks queued", "engine");
      } else {
        stopBackgroundTasks();
        emitClientLog("success", "Engine initialization complete without an adopted leader session — background tasks skipped", "engine");
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
    void (async () => {
      if (statusRef.current !== "connected") return;
      const session = await getAuthedSession();
      if (!session?.user?.id || !nativeVaultEngineTransitionContext(session.user.id)) return;
      emitClientLog("info", "Promoted to leader window — starting adopted background tasks", "engine");
      startBackgroundTasks();
    })();
    return () => stopBackgroundTasks();
  }, [isLeader]);

  // The local engine is part of the desktop application's runtime, not an
  // authenticated cloud resource. Start it as soon as this window mounts so
  // login, logout, and packaged smoke runs all have the same lifecycle. Cloud
  // configuration and the authenticated WebSocket remain gated below on an
  // actual signed-in session.
  useEffect(() => {
    if (statusRef.current !== "connected") {
      void initialize();
    }
  }, [initialize]);

  useEffect(() => {
    mountedRef.current = true;

    const offConnected = engine.on("connected", () =>
      update({ wsConnected: true })
    );
    const offDisconnected = engine.on("disconnected", () =>
      update({ wsConnected: false })
    );

    // The token-push path is gone (SPEC-CUSTODY §10 step 3, D17).
    //
    // `syncTokenToPython`, `pushSessionToEngine`, `pushFreshSessionToEngine` and the forced
    // `refreshSession`/`signOut` self-heal that hung off them all existed to keep a copy of this
    // window's Supabase session alive inside the Python engine. There is no such copy now: the
    // engine asks `matrx-syncd` for a short-lived token whenever it needs one, and the daemon is
    // the only holder of anything renewable on the machine. A window that is closed, asleep or
    // never opened cannot leave the engine stranded, which is the whole of MXL-D-046.

    // `session_refresh_requested` is no longer answered here, and the engine no longer sends it:
    // it asks the daemon directly, which is the one process that can actually mint a token. A
    // handler that logged and did nothing would be worse than none.

    // Re-configure cloud sync and sync JWT to Python whenever auth state changes.
    const authSub = subscribeNativeVaultHostEvents(({ event, session, revision, completion }) => {
        // Fence synchronously in Supabase's callback; the native command and
        // all engine I/O run after its lock is released.
        // Cancel idle work before clearing the engine token. Each cloud task
        // also rechecks its exact subject in case it already left the queue.
        stopBackgroundTasks();
        engine.disconnect();
        update({ wsConnected: false });
        void completion.then(async ({ accepted }) => {
          if (!accepted || nativeVaultEngineTransitionContext(session?.user?.id)?.revision !== revision) return;
          if (session?.user?.id && !nativeVaultEngineTransitionContext(session.user.id)) return;
          if (event === "SIGNED_IN" || event === "INITIAL_SESSION") {
            const accessToken = session?.user?.id ? await getToken() : null;
            if (accessToken && session?.user?.id) {
              if (statusRef.current === "connected" && !wsConnectedRef.current) {
                try {
                  const context = nativeVaultEngineTransitionContext(session.user.id);
                  if (!context) return;
                  await engine.connectWebSocket(context);
                  update({ wsConnected: true });
                } catch (error) {
                  emitClientLog("warn", `Deferred WebSocket connection failed: ${error}`, "engine");
                }
              } else if (statusRef.current !== "connected") {
                void initialize();
              }
              // Skip if initialize() already sent configure within the last 10s
              // to avoid a duplicate call on the INITIAL_SESSION event.
              if (Date.now() - lastCloudConfigureRef.current < 10_000) return;
              try {
                lastCloudConfigureRef.current = Date.now();
                const context = nativeVaultEngineTransitionContext(session.user.id);
                if (!context) return;
                await engine.configureCloudSync(accessToken, session.user.id, context);
                engine.cloudHeartbeat(context).catch((e) => console.warn("[engine] cloudHeartbeat failed:", e));
              } catch (e) {
                console.warn("[engine] configureCloudSync failed (non-critical):", e);
              }
            }
          } else if (event === "TOKEN_REFRESHED") {
            // The daemon rotated. Nothing is pushed anywhere; cloud sync is simply re-armed with
            // the new token, which the daemon has already minted.
            const rotated = session?.user?.id ? await getToken() : null;
            if (rotated && session?.user?.id) {
              try {
                const context = nativeVaultEngineTransitionContext(session.user.id);
                if (!context) return;
                await engine.reconfigureCloudSync(rotated, session.user.id, context);
              } catch (e) {
                console.warn("[engine] reconfigureCloudSync failed (non-critical):", e);
              }
            }
          }
          // A fence accepted this exact session above. Restart only now; a
          // failure returned before any queued task can observe the session.
          if (session?.user?.id && isLeaderRef.current && nativeVaultEngineTransitionContext(session.user.id)) {
            startBackgroundTasks();
          }
        }).catch((error) => {
          emitClientLog("warn", `Native Vault account fence failed: ${error}`, "auth");
          engine.disconnect();
          update({ wsConnected: false });
        });
    });

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
      void (async () => {
        const session = await getAuthedSession();
        const context = nativeVaultEngineTransitionContext(session?.user?.id);
        if (context) await engine.cloudHeartbeat(context);
      })().catch((e) => console.warn("[engine] periodic heartbeat failed:", e));
    }, 300000);

    return () => {
      mountedRef.current = false;
      offConnected();
      offDisconnected();
      authSub();
      clearInterval(healthInterval);
      clearInterval(heartbeatInterval);
      stopBackgroundTasks();
      engine.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { ...state, refresh, restartEngine, engine };
}
