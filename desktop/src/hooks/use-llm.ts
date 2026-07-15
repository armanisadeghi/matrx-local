import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { invokeTauri as tauriInvoke, isTauri } from "@/lib/sidecar";
import { engine } from "@/lib/api";
import { findLlmModelInfo, overlayLlmCatalog } from "@/lib/llm/catalog";
import type { UnlistenFn } from "@tauri-apps/api/event";
import type {
  LlmHardwareResult,
  LlmServerStatus,
  LlmSetupStatus,
  LlmDownloadProgress,
  LlmDownloadCancelledEvent,
  DownloadedLlmModel,
} from "@/lib/llm/types";

async function tauriListen<T>(
  event: string,
  handler: (e: { payload: T }) => void,
): Promise<UnlistenFn> {
  const { listen } = await import("@tauri-apps/api/event");
  return listen<T>(event, handler);
}

const HF_MIGRATION_SESSION_KEY = "matrx_hf_token_migrated_v1";

export interface ServerStartProgress {
  elapsed_secs: number;
  max_secs: number;
  phase: string;
  percent: number;
}

export interface ServerLogLine {
  line: string;
  kind: "loading" | "progress" | "ready" | "error" | "noise" | "info";
}

export interface LlmDownloadQueueEntry {
  filename: string;
  urls: string[];
}

export interface LlmState {
  // Setup
  setupStatus: LlmSetupStatus | null;
  hardwareResult: LlmHardwareResult | null;
  downloadProgress: LlmDownloadProgress | null;
  isDetecting: boolean;
  isDownloading: boolean;
  /** Filename of the model actively being downloaded right now */
  downloadingFilename: string | null;
  /** Models queued to download after the current one finishes */
  downloadQueue: LlmDownloadQueueEntry[];
  isStarting: boolean;
  startingModelName: string | null;
  serverStartProgress: ServerStartProgress | null;
  serverLogs: ServerLogLine[];
  downloadCancelled: boolean;

  // Server
  serverStatus: LlmServerStatus | null;
  downloadedModels: DownloadedLlmModel[];

  /** Hugging Face token is set (engine API Keys or legacy llm.json). */
  hfTokenConfigured: boolean;
  /** True when the last download failed because the repo uses XET storage and no token is set. */
  xetTokenRequired: boolean;
  /** The model that triggered the XET token requirement (so the modal can retry it). */
  xetPendingFilename: string | null;
  xetPendingUrls: string[];

  // Errors
  error: string | null;
}

export interface LlmActions {
  detectHardware: () => Promise<LlmHardwareResult>;
  /** Pass all part URLs in order. Single-file models have one URL; split models have multiple. */
  downloadModel: (
    filename: string,
    urls: string[],
    overrideHfToken?: string,
  ) => Promise<void>;
  /**
   * Add a model to the download queue. If nothing is currently downloading,
   * the download starts immediately. Otherwise it runs after the current one
   * finishes (or fails). Safe to call multiple times — already-queued or
   * already-downloaded models are ignored.
   */
  queueDownload: (filename: string, urls: string[]) => void;
  /**
   * Queue all provided models for download, skipping any already downloaded.
   * Downloads happen sequentially via the queue.
   */
  downloadAll: (models: LlmDownloadQueueEntry[]) => void;
  /** Request cancellation of an in-flight download. */
  cancelDownload: () => Promise<void>;
  /**
   * Copy a local GGUF file into the app's models directory.
   * Returns the final filename it was saved as.
   */
  importLocalModel: (
    sourcePath: string,
    destFilename?: string,
  ) => Promise<string>;
  /** Re-read whether a Hugging Face token is configured (after changing Settings). */
  refreshHfTokenConfigured: () => Promise<void>;
  /** Save a new HF token and immediately retry the pending XET download. */
  saveHfTokenAndRetry: (token: string) => Promise<void>;
  /** Dismiss the XET token modal without retrying. */
  dismissXetModal: () => void;
  startServer: (
    modelFilename: string,
    gpuLayers: number,
    contextLength?: number,
  ) => Promise<LlmServerStatus>;
  stopServer: () => Promise<void>;
  getServerStatus: () => Promise<LlmServerStatus>;
  healthCheck: () => Promise<boolean>;
  checkModelExists: (filename: string) => boolean;
  listModels: () => Promise<DownloadedLlmModel[]>;
  deleteModel: (filename: string) => Promise<void>;
  refreshSetupStatus: () => Promise<void>;
  quickSetup: () => Promise<void>;
  clearError: () => void;
}

export function useLlm(): [LlmState, LlmActions] {
  const [setupStatus, setSetupStatus] = useState<LlmSetupStatus | null>(null);
  const [hardwareResult, setHardwareResult] =
    useState<LlmHardwareResult | null>(null);
  const [downloadProgress, setDownloadProgress] =
    useState<LlmDownloadProgress | null>(null);
  const [isDetecting, setIsDetecting] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [downloadingFilename, setDownloadingFilename] = useState<string | null>(
    null,
  );
  const [downloadQueue, setDownloadQueue] = useState<LlmDownloadQueueEntry[]>(
    [],
  );
  const [isStarting, setIsStarting] = useState(false);
  const [startingModelName, setStartingModelName] = useState<string | null>(
    null,
  );
  const [serverStartProgress, setServerStartProgress] =
    useState<ServerStartProgress | null>(null);
  const [serverLogs, setServerLogs] = useState<ServerLogLine[]>([]);
  const [downloadCancelled, setDownloadCancelled] = useState(false);
  const [serverStatus, setServerStatus] = useState<LlmServerStatus | null>(
    null,
  );
  const [downloadedModels, setDownloadedModels] = useState<
    DownloadedLlmModel[]
  >([]);
  const [error, setError] = useState<string | null>(null);
  const [hfTokenConfigured, setHfTokenConfigured] = useState(false);
  const [xetTokenRequired, setXetTokenRequired] = useState(false);
  const [xetPendingFilename, setXetPendingFilename] = useState<string | null>(
    null,
  );
  const [xetPendingUrls, setXetPendingUrls] = useState<string[]>([]);

  const unlistenRef = useRef<UnlistenFn[]>([]);
  // Ref-based queue so the processor callback always sees latest value
  const downloadQueueRef = useRef<LlmDownloadQueueEntry[]>([]);
  const isDownloadingRef = useRef(false);
  // Stable snapshot for startServer so the callback need not depend on hardwareResult
  const hardwareResultRef = useRef<LlmHardwareResult | null>(null);
  hardwareResultRef.current = hardwareResult;
  // Per-download generation token. cancelDownload force-clears the download
  // state while the cancelled Tauri invoke is still pending; without a token
  // its finally/error paths would clobber the NEXT download's state. Each
  // downloadModel call owns a generation; stale generations no-op.
  const downloadGenerationRef = useRef(0);
  // Late-bound handle so downloadModel's finally can kick the queue without
  // a circular useCallback dependency (processQueue depends on downloadModel).
  const processQueueRef = useRef<() => Promise<void>>(async () => {});

  const migrateLegacyHfTokenIfNeeded = useCallback(async () => {
    if (!isTauri()) return;
    if (
      typeof sessionStorage !== "undefined" &&
      sessionStorage.getItem(HF_MIGRATION_SESSION_KEY) === "1"
    ) {
      return;
    }
    const legacy = await tauriInvoke<string | null>("get_hf_token").catch(
      () => null,
    );
    if (!legacy?.trim()) {
      if (typeof sessionStorage !== "undefined")
        sessionStorage.setItem(HF_MIGRATION_SESSION_KEY, "1");
      return;
    }
    if (!engine.engineUrl) return;
    try {
      await engine.put("/settings/api-keys/huggingface", {
        key: legacy.trim(),
      });
      await tauriInvoke("save_hf_token", { token: "" });
      if (typeof sessionStorage !== "undefined")
        sessionStorage.setItem(HF_MIGRATION_SESSION_KEY, "1");
    } catch {
      /* engine offline or unauthenticated — retry later */
    }
  }, []);

  const refreshHfTokenConfigured = useCallback(async () => {
    if (!isTauri()) {
      setHfTokenConfigured(false);
      return;
    }
    await migrateLegacyHfTokenIfNeeded();
    try {
      if (engine.engineUrl) {
        const data = (await engine.get("/settings/api-keys")) as {
          providers: { provider: string; configured: boolean }[];
        };
        const hf = data.providers?.find((p) => p.provider === "huggingface");
        if (hf?.configured) {
          setHfTokenConfigured(true);
          return;
        }
      }
    } catch {
      /* fall through to legacy */
    }
    const legacy = await tauriInvoke<string | null>("get_hf_token").catch(
      () => null,
    );
    setHfTokenConfigured(!!legacy?.trim());
  }, [migrateLegacyHfTokenIfNeeded]);

  // Clean up event listeners on unmount
  useEffect(() => {
    return () => {
      unlistenRef.current.forEach((fn) => fn());
      unlistenRef.current = [];
    };
  }, []);

  // Load initial setup status and listen for server/cancel events
  useEffect(() => {
    if (!isTauri()) return;

    refreshSetupStatus();
    void refreshHfTokenConfigured();

    let mounted = true;
    const setupListeners = async () => {
      // Remove any previously registered listeners before adding new ones.
      // This prevents accumulation if the effect ever re-runs (e.g. due to
      // dependency identity change).  The separate cleanup useEffect at [] also
      // handles the unmount case; this guard covers the re-run case.
      const existing = unlistenRef.current.splice(0);
      existing.forEach((fn) => fn());
      const unlistenReady = await tauriListen<LlmServerStatus>(
        "llm-server-ready",
        (event) => {
          if (mounted) {
            setServerStatus(event.payload);
            setStartingModelName(null);
            setServerStartProgress(null);
            setServerLogs([]);
            // Notify Python engine so agents can route to the local model.
            engine
              .connectLocalLlm(
                event.payload.port,
                event.payload.model_name ?? "",
              )
              .catch((err) => {
                console.warn(
                  "[use-llm] Failed to notify engine of local LLM start:",
                  err,
                );
              });
          }
        },
      );
      const unlistenStarting = await tauriListen<{
        model_filename: string;
        port: number;
      }>("llm-server-starting", (event) => {
        if (mounted) {
          setStartingModelName(event.payload.model_filename);
          setServerStartProgress(null);
          setServerLogs([]);
        }
      });
      const unlistenProgress = await tauriListen<ServerStartProgress>(
        "llm-server-progress",
        (event) => {
          if (mounted) setServerStartProgress(event.payload);
        },
      );
      const unlistenLog = await tauriListen<ServerLogLine>(
        "llm-server-log",
        (event) => {
          if (!mounted) return;
          setServerLogs((prev) => {
            const next = [...prev, event.payload];
            // Keep last 50 lines
            return next.length > 50 ? next.slice(next.length - 50) : next;
          });
        },
      );
      const unlistenStopped = await tauriListen<void>(
        "llm-server-stopped",
        () => {
          if (mounted) {
            setServerStatus((prev) =>
              prev ? { ...prev, running: false, port: 0 } : null,
            );
            setStartingModelName(null);
            setServerStartProgress(null);
            // Notify Python engine so it deregisters the local model.
            engine.disconnectLocalLlm().catch((err) => {
              console.warn(
                "[use-llm] Failed to notify engine of local LLM stop:",
                err,
              );
            });
          }
        },
      );
      const unlistenCancelled = await tauriListen<LlmDownloadCancelledEvent>(
        "llm-download-cancelled",
        () => {
          if (mounted) {
            setDownloadCancelled(true);
            setIsDownloading(false);
            isDownloadingRef.current = false;
            setDownloadingFilename(null);
            setDownloadProgress(null);
            // Do NOT clear the queue — remaining downloads should continue
            // after the cancelled one. processQueue() is called by cancelDownload().
          }
        },
      );
      unlistenRef.current.push(
        unlistenReady,
        unlistenStarting,
        unlistenProgress,
        unlistenLog,
        unlistenStopped,
        unlistenCancelled,
      );
    };
    setupListeners();

    // Reconcile an ALREADY-RUNNING llama-server with the Python engine.
    // The auto-start path in lib.rs emits llm-server-ready during Tauri
    // setup — before this hook mounts — and Tauri does not replay events to
    // late listeners, so without this pass the engine never learns the
    // server exists and agents cannot route to the local model. Retries
    // cover the window where engine discovery (baseUrl) hasn't completed.
    const reconcileEngineRegistration = async () => {
      const RETRY_DELAY_MS = 3000;
      const MAX_ATTEMPTS = 10;
      for (let attempt = 0; attempt < MAX_ATTEMPTS && mounted; attempt++) {
        try {
          const status = await tauriInvoke<LlmServerStatus>(
            "get_llm_server_status",
          );
          if (!status.running || !status.port) return; // nothing to reconcile
          if (mounted) setServerStatus(status);
          try {
            const engineStatus = await engine.getLocalLlmStatus();
            if (engineStatus.available && engineStatus.port === status.port) {
              return; // engine already knows about this server
            }
          } catch {
            // Engine not discovered yet — fall through to connect (which
            // throws in that case) and retry on the next attempt.
          }
          await engine.connectLocalLlm(status.port, status.model_name ?? "");
          console.log(
            "[use-llm] Reconciled auto-started llama-server with engine (port",
            status.port,
            ")",
          );
          return;
        } catch {
          // Engine not up / not discovered yet — retry below.
        }
        await new Promise((resolve) => setTimeout(resolve, RETRY_DELAY_MS));
      }
      if (mounted) {
        console.warn(
          "[use-llm] Could not register running llama-server with the engine after retries — local model will be unavailable to agents",
        );
      }
    };
    void reconcileEngineRegistration();

    return () => {
      mounted = false;
      // Also unlisten here so that if this effect re-runs (dependency change),
      // listeners from this run are removed before the next run adds new ones.
      // The [] cleanup useEffect is the final guard for the unmount case.
      unlistenRef.current.forEach((fn) => fn());
      unlistenRef.current = [];
    };
  }, [refreshHfTokenConfigured]);

  const refreshSetupStatus = useCallback(async () => {
    if (!isTauri()) return;
    try {
      const status = await tauriInvoke<LlmSetupStatus>("get_llm_setup_status");
      setSetupStatus(status);
      if (status.server_running) {
        setServerStatus({
          running: true,
          port: status.server_port,
          model_path: "",
          model_name: status.server_model,
          gpu_layers: 0,
          context_length: 0,
        });
      }
      const models = await tauriInvoke<DownloadedLlmModel[]>("list_llm_models");
      setDownloadedModels(models);
    } catch {
      // Not critical — LLM features may not be available
    }
  }, []);

  const detectHardware = useCallback(async () => {
    setIsDetecting(true);
    setError(null);
    try {
      // Rust owns hardware probing + the recommendation; the browsable model
      // list is overlaid with the remote catalog (compiled consts = fallback).
      const result = await overlayLlmCatalog(
        await tauriInvoke<LlmHardwareResult>("detect_llm_hardware"),
      );
      setHardwareResult(result);
      return result;
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      throw e;
    } finally {
      setIsDetecting(false);
    }
  }, []);

  /**
   * Execute a single download. Does NOT manage queue state — that's done
   * by processQueue. Direct callers (quickSetup) can call this directly.
   */
  const downloadModel = useCallback(
    async (filename: string, urls: string[], overrideHfToken?: string) => {
      const generation = ++downloadGenerationRef.current;
      setIsDownloading(true);
      isDownloadingRef.current = true;
      setDownloadingFilename(filename);
      setDownloadProgress(null);
      setDownloadCancelled(false);
      setXetTokenRequired(false);
      setXetPendingFilename(null);
      setXetPendingUrls([]);
      setError(null);

      const unlisten = await tauriListen<LlmDownloadProgress>(
        "llm-download-progress",
        (event) => {
          // A cancelled download's invoke can still emit a trailing event —
          // don't let it overwrite the next download's progress.
          if (downloadGenerationRef.current === generation) {
            setDownloadProgress(event.payload);
          }
        },
      );

      try {
        await migrateLegacyHfTokenIfNeeded();
        // Use override token (e.g. freshly entered in wizard) when provided so
        // we don't race against the async engine fetch which could return null.
        const hfTok =
          overrideHfToken?.trim() ||
          (await engine.getHuggingfaceTokenForDownloads());
        await tauriInvoke("download_llm_model", {
          filename,
          urls,
          hfToken: hfTok ?? null,
        });
        void refreshHfTokenConfigured();
        if (downloadGenerationRef.current === generation) {
          setDownloadProgress(null);
        }
        const models =
          await tauriInvoke<DownloadedLlmModel[]>("list_llm_models");
        setDownloadedModels(models);
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        // Stale generation (cancelled, or a newer download owns the state):
        // don't touch shared UI state — just propagate.
        if (downloadGenerationRef.current !== generation) throw e;
        if (
          msg.startsWith("XET_TOKEN_REQUIRED") ||
          msg.startsWith("XET_TOKEN_INVALID")
        ) {
          setXetTokenRequired(true);
          setXetPendingFilename(filename);
          setXetPendingUrls(urls);
          // Don't set error — the modal handles this gracefully
        } else if (!msg.toLowerCase().includes("cancel")) {
          setError(msg);
          // Forward the error to the Python engine log so it appears in the
          // "Copy Issue Report" output. Rust eprintln! covers the sidecar IPC
          // channel; this covers the engine SSE log channel.
          if (engine.engineUrl) {
            engine
              .post("/log", {
                level: "error",
                source: "llm-download",
                message: `LLM download failed for '${filename}': ${msg}`,
              })
              .catch(() => {
                /* best-effort — don't cascade if engine is offline */
              });
          }
        }
        throw e;
      } finally {
        unlisten();
        if (downloadGenerationRef.current === generation) {
          setIsDownloading(false);
          isDownloadingRef.current = false;
          setDownloadingFilename(null);
          // Direct callers (quickSetup) bypass the queue entirely — kick the
          // processor so queued items don't strand behind a direct download.
          setTimeout(() => void processQueueRef.current(), 0);
        }
      }
    },
    [migrateLegacyHfTokenIfNeeded, refreshHfTokenConfigured],
  );

  /**
   * Sequential queue processor. Pops the next entry from downloadQueueRef and
   * downloads it. Calls itself recursively until the queue is empty.
   * Continues to the next item even if the current one fails (so a bad download
   * doesn't block the rest of the queue).
   */
  const processQueue = useCallback(async () => {
    if (isDownloadingRef.current) return;
    const next = downloadQueueRef.current.shift();
    if (!next) return;
    setDownloadQueue([...downloadQueueRef.current]);
    try {
      await downloadModel(next.filename, next.urls);
    } catch {
      // Error already set by downloadModel — continue to next item
    }
    // Recurse: process the next item in the queue
    void processQueue();
  }, [downloadModel]);
  processQueueRef.current = processQueue;

  const queueDownload = useCallback(
    (filename: string, urls: string[]) => {
      // Skip if already downloaded
      const alreadyDownloaded = downloadedModels.some(
        (m) => m.filename === filename,
      );
      if (alreadyDownloaded) return;
      // Skip if already queued
      const alreadyQueued = downloadQueueRef.current.some(
        (e) => e.filename === filename,
      );
      if (alreadyQueued) return;
      // Skip if currently downloading this exact file
      if (isDownloadingRef.current && downloadingFilename === filename) return;

      if (!isDownloadingRef.current) {
        // Nothing active — start immediately
        void downloadModel(filename, urls)
          .then(() => void processQueue())
          .catch(() => void processQueue());
      } else {
        // Something is active — push to queue
        downloadQueueRef.current.push({ filename, urls });
        setDownloadQueue([...downloadQueueRef.current]);
      }
    },
    [downloadModel, downloadedModels, downloadingFilename, processQueue],
  );

  const downloadAll = useCallback(
    (models: LlmDownloadQueueEntry[]) => {
      const downloadedSet = new Set(downloadedModels.map((m) => m.filename));
      const queuedSet = new Set(
        downloadQueueRef.current.map((e) => e.filename),
      );
      const activeFilename = isDownloadingRef.current
        ? downloadingFilename
        : null;

      for (const m of models) {
        if (downloadedSet.has(m.filename)) continue;
        if (queuedSet.has(m.filename)) continue;
        if (activeFilename === m.filename) continue;
        downloadQueueRef.current.push({ filename: m.filename, urls: m.urls });
        queuedSet.add(m.filename);
      }
      setDownloadQueue([...downloadQueueRef.current]);

      if (!isDownloadingRef.current) {
        void processQueue();
      }
    },
    [downloadedModels, downloadingFilename, processQueue],
  );

  const cancelDownload = useCallback(async () => {
    // Cancel only the CURRENT active download — do NOT wipe the queue.
    // The remaining queued items will continue after the cancelled download settles.
    // Force state to idle immediately so UI unblocks even if the Tauri
    // llm-download-cancelled event never arrives.
    // Bump the generation so the cancelled invoke's still-pending
    // finally/error paths become no-ops instead of clobbering the state of
    // whatever download starts next.
    downloadGenerationRef.current++;
    setIsDownloading(false);
    isDownloadingRef.current = false;
    setDownloadingFilename(null);
    setDownloadProgress(null);
    setDownloadCancelled(true);
    try {
      await tauriInvoke("cancel_llm_download");
    } catch {
      // Ignore errors — state already cleared above
    }
    // Resume processing any remaining queued items after a short delay
    // so the Rust cancellation has time to settle before the next download starts.
    setTimeout(() => void processQueue(), 500);
  }, [processQueue]);

  const importLocalModel = useCallback(
    async (sourcePath: string, destFilename = "") => {
      setError(null);
      try {
        const filename = await tauriInvoke<string>("import_local_llm_model", {
          sourcePath,
          destFilename,
        });
        // Refresh the model list so the new model appears immediately
        const models =
          await tauriInvoke<DownloadedLlmModel[]>("list_llm_models");
        setDownloadedModels(models);
        return filename;
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setError(msg);
        throw e;
      }
    },
    [],
  );

  const startServer = useCallback(
    async (
      modelFilename: string,
      gpuLayers: number,
      contextLength?: number,
    ) => {
      setIsStarting(true);
      setStartingModelName(modelFilename);
      setServerStartProgress(null);
      setServerLogs([]);
      setError(null);
      try {
        const hw = hardwareResultRef.current;
        const catalogCtx = hw?.all_models.find(
          (m) =>
            m.filename === modelFilename ||
            m.variants?.some((v) => v.filename === modelFilename),
        )?.context_length;
        const { resolveContextLength } =
          await import("@/lib/llm/contextLength");
        const resolvedCtx = await resolveContextLength(modelFilename, {
          ...(contextLength !== undefined ? { explicit: contextLength } : {}),
          ...(catalogCtx !== undefined ? { catalogContext: catalogCtx } : {}),
        });
        const status = await tauriInvoke<LlmServerStatus>("start_llm_server", {
          modelFilename,
          gpuLayers,
          contextLength: resolvedCtx,
        });
        setServerStatus(status);
        return status;
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setError(msg);
        throw e;
      } finally {
        setIsStarting(false);
        setStartingModelName(null);
        setServerStartProgress(null);
      }
    },
    [],
  );

  const stopServer = useCallback(async () => {
    setError(null);
    try {
      await tauriInvoke("stop_llm_server");
      setServerStatus((prev) =>
        prev ? { ...prev, running: false, port: 0 } : null,
      );
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
    }
  }, []);

  const getServerStatus = useCallback(async () => {
    const status = await tauriInvoke<LlmServerStatus>("get_llm_server_status");
    setServerStatus(status);
    return status;
  }, []);

  const healthCheck = useCallback(async () => {
    return tauriInvoke<boolean>("check_llm_server_health");
  }, []);

  const checkModelExists = useCallback(
    (filename: string): boolean => {
      // Synchronous check against downloaded models list (already loaded from Rust)
      return downloadedModels.some((m) => m.filename === filename);
    },
    [downloadedModels],
  );

  const listModels = useCallback(async () => {
    const models = await tauriInvoke<DownloadedLlmModel[]>("list_llm_models");
    setDownloadedModels(models);
    return models;
  }, []);

  const deleteModel = useCallback(
    async (filename: string) => {
      setError(null);
      try {
        await tauriInvoke("delete_llm_model", { filename });
        const models =
          await tauriInvoke<DownloadedLlmModel[]>("list_llm_models");
        setDownloadedModels(models);
        await refreshSetupStatus();
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setError(msg);
        throw e;
      }
    },
    [refreshSetupStatus],
  );

  const clearError = useCallback(() => {
    setError(null);
    setDownloadCancelled(false);
    setXetTokenRequired(false);
    setXetPendingFilename(null);
    setXetPendingUrls([]);
  }, []);

  const dismissXetModal = useCallback(() => {
    setXetTokenRequired(false);
    setXetPendingFilename(null);
    setXetPendingUrls([]);
  }, []);

  const saveHfTokenAndRetry = useCallback(
    async (token: string) => {
      const trimmed = token.trim();
      if (!trimmed) return;

      // Save to llm.json via Rust first — this is synchronous, always available,
      // and ensures the token survives even if the Python engine is unreachable.
      await tauriInvoke("save_hf_token", { token: trimmed }).catch(() => {
        /* non-fatal — Rust save may fail in dev browser mode */
      });

      // Also save to Python engine SQLite so the key appears in Settings → API Keys.
      if (engine.engineUrl) {
        try {
          await engine.put("/settings/api-keys/huggingface", { key: trimmed });
        } catch {
          /* best-effort — llm.json copy above is the guaranteed fallback */
        }
        await refreshHfTokenConfigured();
      }

      const pendingFilename = xetPendingFilename;
      const pendingUrls = xetPendingUrls;
      setXetTokenRequired(false);
      setXetPendingFilename(null);
      setXetPendingUrls([]);
      // Pass the token directly to the retry so we don't depend on an async
      // engine fetch that could return null if the engine is momentarily busy.
      if (pendingFilename && pendingUrls.length > 0) {
        await downloadModel(pendingFilename, pendingUrls, trimmed);
      }
    },
    [
      xetPendingFilename,
      xetPendingUrls,
      refreshHfTokenConfigured,
      downloadModel,
    ],
  );

  // One-click setup: detect hardware, download recommended model, start server
  const quickSetup = useCallback(async () => {
    setError(null);
    try {
      const hw = await detectHardware();

      const alreadyDownloaded = downloadedModels.some(
        (m) => m.filename === hw.recommended_filename,
      );
      if (!alreadyDownloaded) {
        // findLlmModelInfo falls back to the compiled Rust consts when a
        // DB catalog edit removed the recommended filename from the
        // overlaid list — one-click setup must survive that.
        const modelInfo = findLlmModelInfo(hw, hw.recommended_filename);
        if (!modelInfo) {
          throw new Error(
            `Model info not found for ${hw.recommended_filename}`,
          );
        }
        await downloadModel(hw.recommended_filename, modelInfo.all_part_urls);
      }

      await startServer(hw.recommended_filename, hw.recommended_gpu_layers);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (!msg.toLowerCase().includes("cancel")) {
        setError(`Setup failed: ${msg}`);
      }
      throw e;
    }
  }, [detectHardware, downloadedModels, downloadModel, startServer]);

  const state: LlmState = {
    setupStatus,
    hardwareResult,
    downloadProgress,
    isDetecting,
    isDownloading,
    downloadingFilename,
    downloadQueue,
    isStarting,
    startingModelName,
    serverStartProgress,
    serverLogs,
    downloadCancelled,
    serverStatus,
    downloadedModels,
    hfTokenConfigured,
    xetTokenRequired,
    xetPendingFilename,
    xetPendingUrls,
    error,
  };

  const actions: LlmActions = useMemo(
    () => ({
      detectHardware,
      downloadModel,
      queueDownload,
      downloadAll,
      cancelDownload,
      importLocalModel,
      refreshHfTokenConfigured,
      saveHfTokenAndRetry,
      dismissXetModal,
      startServer,
      stopServer,
      getServerStatus,
      healthCheck,
      checkModelExists,
      listModels,
      deleteModel,
      refreshSetupStatus,
      quickSetup,
      clearError,
    }),
    [
      detectHardware,
      downloadModel,
      queueDownload,
      downloadAll,
      cancelDownload,
      importLocalModel,
      refreshHfTokenConfigured,
      saveHfTokenAndRetry,
      dismissXetModal,
      startServer,
      stopServer,
      getServerStatus,
      healthCheck,
      checkModelExists,
      listModels,
      deleteModel,
      refreshSetupStatus,
      quickSetup,
      clearError,
    ],
  );

  return [state, actions];
}
