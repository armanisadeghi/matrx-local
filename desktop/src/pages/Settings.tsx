import { useState, useEffect, useCallback, useRef } from "react";
import {
  Settings as SettingsIcon,
  Server,
  Globe,
  Palette,
  RefreshCw,
  Power,
  FolderOpen,
  Download,
  Loader2,
  CheckCircle2,
  HelpCircle,
  ShieldCheck,
  XCircle,
  Cloud,
  CloudOff,
  ArrowUpFromLine,
  ArrowDownToLine,
  Shield,
  Monitor,
  Wifi,
  AlertCircle,
  Copy,
  CheckCheck,
  Cpu,
  CircleCheck,
  CircleDashed,
  ExternalLink,
  Ban,
  Plus,
  X,
  HardDrive,
  RotateCcw,
  Pencil,
  Check,
  Radio,
  QrCode,
  Link,
  KeyRound,
  Eye,
  EyeOff,
  Trash2,
  FileUp,
  Mic,
  Speaker,
  Camera,
  Network,
  Layers,
  MemoryStick,
} from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { CloudAgentToolsCard } from "@/components/settings/CloudAgentToolsCard";
import { SubTabBar } from "@/components/layout/SubTabBar";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { useLocation } from "react-router-dom";
import type { EngineStatus } from "@/hooks/use-engine";
import { engine } from "@/lib/api";
import { useAccessHealthContext } from "@/contexts/AccessHealthContext";
import type {
  ProxyStatus,
  InstanceInfo,
  Capability,
  HardwareProfile,
} from "@/lib/api";
import type { useAuth } from "@/hooks/use-auth";
import type { Theme } from "@/hooks/use-theme";

declare const __APP_VERSION__: string;
import { isTauri } from "@/lib/sidecar";
import { systemPrompts, builtinPrompts } from "@/lib/system-prompts";
import type {
  AutoUpdateState,
  AutoUpdateActions,
} from "@/hooks/use-auto-update";
import {
  loadSettings,
  saveSetting,
  saveSettings,
  syncAllSettings,
  broadcastSettingsChanged,
  settingsToCloud,
  mergeCloudSettings,
  type AppSettings,
} from "@/lib/settings";
import type { StoragePath, StoragePathStats } from "@/lib/api";
import {
  parseEnvBlock,
  refreshApiKeyPatterns,
  type ParsedEnvEntry,
} from "@/lib/api-key-patterns";
import { Textarea } from "@/components/ui/textarea";
import {
  ENGINE_DEFAULT_URL,
  ENGINE_PORT_BASE,
  ENGINE_PORT_RANGE_LABEL,
} from "@/lib/engine-ports";
import { FilesystemIndexSettings } from "@/features/filesystem/FilesystemIndexSettings";

type AuthActions = ReturnType<typeof useAuth>;

/** Tri-state, mirroring the engine's ApiKeyValidation. `unknown` means we could
 *  not reach the provider — it must never be rendered as "your key is bad". */
type ApiKeyVerdict = "valid" | "invalid" | "unknown" | "unsupported";

interface ApiKeyValidation {
  provider: string;
  verdict: ApiKeyVerdict;
  message: string;
  account: string | null;
  status_code: number | null;
}

interface ApiKeyProviderStatus {
  provider: string;
  label: string;
  description: string;
  configured: boolean;
  /** Provider exposes a free auth-only endpoint we can check the key against. */
  testable: boolean;
  last_verdict: ApiKeyVerdict | null;
  last_account: string | null;
  last_checked_at: string | null;
}

/** How each verdict renders. `unknown` is deliberately neutral-amber, not red:
 *  we could not reach the provider, which is our problem, not the key's — and
 *  showing red would send the user off to rotate a credential that works. */
const VERDICT_STYLES: Record<
  ApiKeyVerdict,
  {
    label: string;
    Icon: typeof CheckCircle2;
    className: string;
    textClassName: string;
  }
> = {
  valid: {
    label: "Valid",
    Icon: CheckCircle2,
    className: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
    textClassName: "text-emerald-600 dark:text-emerald-400",
  },
  invalid: {
    label: "Rejected",
    Icon: XCircle,
    className: "bg-red-500/15 text-red-600 dark:text-red-400",
    textClassName: "text-red-600 dark:text-red-400",
  },
  unknown: {
    label: "Couldn't check",
    Icon: HelpCircle,
    className: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
    textClassName: "text-amber-600 dark:text-amber-400",
  },
  unsupported: {
    label: "Not testable",
    Icon: HelpCircle,
    className: "bg-muted text-muted-foreground",
    textClassName: "text-muted-foreground",
  },
};

interface SettingsProps {
  engineStatus: EngineStatus;
  engineUrl: string | null;
  engineVersion: string;
  onRefresh: () => void;
  auth: AuthActions;
  theme: Theme;
  setTheme: (t: Theme) => void;
  updateState?: AutoUpdateState;
  updateActions?: AutoUpdateActions;
}

export function Settings({
  engineStatus,
  engineUrl,
  engineVersion,
  onRefresh,
  auth,
  theme,
  setTheme,
  updateState,
  updateActions,
}: SettingsProps) {
  const location = useLocation();
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [activeTab, setActiveTab] = useState(() => {
    const params = new URLSearchParams(location.search);
    return params.get("tab") ?? "general";
  });
  const [restarting, setRestarting] = useState(false);
  const [restartError, setRestartError] = useState<string | null>(null);

  // Respond to ?tab= deep links. The page stays mounted (AppLayout keep-alive),
  // so the useState initializer above only runs once — navigations like
  // navigate("/settings?tab=api-keys") must be handled here.
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const tab = params.get("tab");
    if (tab) setActiveTab(tab);
  }, [location.search]);

  // Proxy state
  const [proxyStatus, setProxyStatus] = useState<ProxyStatus | null>(null);
  const [proxyTesting, setProxyTesting] = useState(false);
  const [proxyTestResult, setProxyTestResult] = useState<string | null>(null);

  // Cloud sync state
  const [syncing, setSyncing] = useState(false);
  const [syncStatus, setSyncStatus] = useState<string | null>(null);
  const [instanceInfo, setInstanceInfo] = useState<InstanceInfo | null>(null);
  const [instances, setInstances] = useState<InstanceInfo[]>([]);
  const [copied, setCopied] = useState(false);

  // Capabilities state
  const [capabilities, setCapabilities] = useState<Capability[]>([]);
  const [installingId, setInstallingId] = useState<string | null>(null);
  const [installProgress, setInstallProgress] = useState<
    Record<string, { percent: number; message: string }>
  >({});
  const [installResult, setInstallResult] = useState<
    Record<string, { success: boolean; message: string }>
  >({});
  const capabilitySseCleanup = useRef<(() => void) | null>(null);

  // Tunnel / remote access state
  const [tunnelStatus, setTunnelStatus] = useState<{
    running: boolean;
    url: string | null;
    ws_url: string | null;
    uptime_seconds: number;
    mode: string;
  } | null>(null);
  const [tunnelLoading, setTunnelLoading] = useState(false);
  // True while the initial GET /tunnel/status fetch is in-flight.
  // Used to show a neutral loading state instead of a false "Stopped/OFF".
  const [tunnelFetching, setTunnelFetching] = useState(false);
  const [tunnelCopied, setTunnelCopied] = useState(false);

  // Forbidden URLs state
  const [forbiddenUrls, setForbiddenUrls] = useState<string[]>([]);

  // Storage paths state
  const [storagePaths, setStoragePaths] = useState<StoragePath[]>([]);
  // Shared access-health store: path changes re-probe server-side; refresh
  // the store so any stale degraded banner clears immediately.
  const { refresh: refreshAccessHealth } = useAccessHealthContext().actions;
  const [pathEditing, setPathEditing] = useState<string | null>(null); // name of path being edited
  const [pathEditValue, setPathEditValue] = useState("");
  const [pathSaving, setPathSaving] = useState<string | null>(null);
  const [pathError, setPathError] = useState<string | null>(null);
  const [pathStats, setPathStats] = useState<Record<string, StoragePathStats>>(
    {},
  );
  const [statsLoading, setStatsLoading] = useState<Record<string, boolean>>({});
  const [newForbiddenUrl, setNewForbiddenUrl] = useState("");
  const [forbiddenSaving, setForbiddenSaving] = useState(false);

  // API key state
  const [apiKeyProviders, setApiKeyProviders] = useState<
    ApiKeyProviderStatus[]
  >([]);

  // Exact action-needed deep links focus the provider that can resolve the
  // requirement. Settings is keep-alive mounted, so rerun when the provider
  // cards finish loading as well as when the URL changes.
  useEffect(() => {
    const provider = new URLSearchParams(location.search).get("provider");
    if (activeTab !== "api-keys" || !provider || apiKeyProviders.length === 0) {
      return;
    }
    const frame = requestAnimationFrame(() => {
      const card = document.getElementById(`api-key-provider-${provider}`);
      card?.scrollIntoView({ behavior: "smooth", block: "center" });
      card?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [activeTab, apiKeyProviders.length, location.search]);
  const [apiKeyInputs, setApiKeyInputs] = useState<Record<string, string>>({});
  const [apiKeyVisible, setApiKeyVisible] = useState<Record<string, boolean>>(
    {},
  );
  const [apiKeySaving, setApiKeySaving] = useState<Record<string, boolean>>({});
  const [apiKeyDeleting, setApiKeyDeleting] = useState<Record<string, boolean>>(
    {},
  );
  const [apiKeyMessages, setApiKeyMessages] = useState<
    Record<string, { ok: boolean; text: string }>
  >({});
  const [apiKeyTesting, setApiKeyTesting] = useState<Record<string, boolean>>(
    {},
  );
  // Live verdict from the current session's test, which supersedes the stored
  // last_verdict on the provider row.
  const [apiKeyVerdicts, setApiKeyVerdicts] = useState<
    Record<string, ApiKeyValidation>
  >({});
  const [testingAllKeys, setTestingAllKeys] = useState(false);

  // Hardware profile state
  const [hardwareProfile, setHardwareProfile] =
    useState<HardwareProfile | null>(null);
  const [hardwareLoading, setHardwareLoading] = useState(false);
  const [hardwareError, setHardwareError] = useState<string | null>(null);

  // Bulk import state
  const [bulkImportOpen, setBulkImportOpen] = useState(false);
  const [bulkEnvText, setBulkEnvText] = useState("");
  const [bulkParsed, setBulkParsed] = useState<ParsedEnvEntry[]>([]);
  const [bulkSelected, setBulkSelected] = useState<Set<string>>(new Set());
  const [bulkSaving, setBulkSaving] = useState(false);
  const [bulkResult, setBulkResult] = useState<{
    saved: string[];
    skipped: string[];
    errors: Record<string, string>;
  } | null>(null);
  // Per-entry editable values, custom provider mappings, and value visibility
  const [bulkEditedValues, setBulkEditedValues] = useState<
    Record<string, string>
  >({});
  const [bulkCustomMapping, setBulkCustomMapping] = useState<
    Record<string, string>
  >({});
  const [bulkShowValues, setBulkShowValues] = useState<Record<string, boolean>>(
    {},
  );

  useEffect(() => {
    loadSettings().then(setSettings);
    loadForbiddenUrls();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Reload settings when another part of the app (e.g. Configurations page) saves changes.
  useEffect(() => {
    const onChanged = () => {
      loadSettings().then(setSettings);
      // Also reload instance info so Computer Name displays the new value immediately.
      if (engineStatus === "connected") loadInstanceInfo();
    };
    window.addEventListener("matrx-settings-changed", onChanged);
    return () =>
      window.removeEventListener("matrx-settings-changed", onChanged);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [engineStatus]);

  useEffect(() => {
    if (engineStatus !== "connected") return;
    loadProxyStatus();
    loadCapabilities();
    loadStoragePaths();
    loadTunnelStatus();
    loadApiKeyStatus();
    loadForbiddenUrls();
    // Instance info depends on cloud sync being configured (JWT pushed to Python).
    // Load immediately for instance hardware info, then retry after 4s to catch
    // the case where configureCloudSync hasn't finished yet and list_instances
    // returns [] because the engine isn't authenticated to Supabase yet.
    loadInstanceInfo();
    const retry = setTimeout(() => loadInstanceInfo(), 4000);
    return () => clearTimeout(retry);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [engineStatus]);

  // Re-load remote tab data whenever the user navigates to it.
  useEffect(() => {
    if (activeTab === "remote" && engineStatus === "connected") {
      loadTunnelStatus();
      loadInstanceInfo();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab]);

  // Load hardware profile when the system tab becomes active.
  useEffect(() => {
    if (
      activeTab === "system" &&
      engineStatus === "connected" &&
      !hardwareProfile
    ) {
      loadHardwareProfile();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, engineStatus]);

  const loadHardwareProfile = useCallback(async () => {
    if (engineStatus !== "connected") return;
    setHardwareLoading(true);
    setHardwareError(null);
    try {
      const result = await engine.getHardware();
      setHardwareProfile(result.profile);
    } catch (err) {
      setHardwareError(String(err));
    } finally {
      setHardwareLoading(false);
    }
  }, [engineStatus]);

  const refreshHardwareProfile = useCallback(async () => {
    if (engineStatus !== "connected") return;
    setHardwareLoading(true);
    setHardwareError(null);
    try {
      const result = await engine.refreshHardware();
      setHardwareProfile(result.profile);
    } catch (err) {
      setHardwareError(String(err));
    } finally {
      setHardwareLoading(false);
    }
  }, [engineStatus]);

  const loadProxyStatus = useCallback(async () => {
    if (engineStatus !== "connected") return;
    try {
      const status = await engine.proxyStatus();
      setProxyStatus(status);
    } catch {
      // Engine may not support proxy yet
    }
  }, [engineStatus]);

  const loadInstanceInfo = useCallback(async () => {
    if (engineStatus !== "connected") return;
    try {
      const info = await engine.getInstanceInfo();
      setInstanceInfo(info);
    } catch {
      // Non-critical
    }
    try {
      const result = await engine.listInstances();
      setInstances(result.instances || []);
    } catch {
      // Non-critical
    }
  }, [engineStatus]);

  const loadForbiddenUrls = useCallback(async () => {
    if (engineStatus !== "connected") return;
    try {
      const data = (await engine.get("/settings/forbidden-urls")) as {
        urls?: string[];
      };
      setForbiddenUrls(data?.urls ?? []);
    } catch {
      /* non-critical */
    }
  }, [engineStatus]);

  const loadStoragePaths = useCallback(async () => {
    if (engineStatus !== "connected") return;
    try {
      const paths = await engine.getStoragePaths();
      setStoragePaths(paths);
    } catch {
      /* non-critical */
    }
  }, [engineStatus]);

  const loadPathStats = useCallback(
    async (names: string[]) => {
      if (engineStatus !== "connected") return;
      setStatsLoading(Object.fromEntries(names.map((n) => [n, true])));
      await Promise.all(
        names.map(async (name) => {
          try {
            const stats = await engine.getStoragePathStats(name);
            setPathStats((prev) => ({ ...prev, [name]: stats }));
          } catch {
            // non-critical
          } finally {
            setStatsLoading((prev) => ({ ...prev, [name]: false }));
          }
        }),
      );
    },
    [engineStatus],
  );

  const startEditPath = useCallback((p: StoragePath) => {
    setPathEditing(p.name);
    setPathEditValue(p.current);
    setPathError(null);
  }, []);

  const cancelEditPath = useCallback(() => {
    setPathEditing(null);
    setPathEditValue("");
    setPathError(null);
  }, []);

  const savePathEdit = useCallback(
    async (name: string) => {
      setPathSaving(name);
      setPathError(null);
      try {
        const updated = await engine.setStoragePath(name, pathEditValue);
        setStoragePaths((prev) =>
          prev.map((p) => (p.name === name ? updated : p)),
        );
        setPathEditing(null);
        void refreshAccessHealth();
      } catch (err) {
        setPathError(err instanceof Error ? err.message : "Failed to set path");
      } finally {
        setPathSaving(null);
      }
    },
    [pathEditValue, refreshAccessHealth],
  );

  const resetPathToDefault = useCallback(async (name: string) => {
    setPathSaving(name);
    setPathError(null);
    try {
      const updated = await engine.resetStoragePath(name);
      setStoragePaths((prev) =>
        prev.map((p) => (p.name === name ? updated : p)),
      );
      void refreshAccessHealth();
    } catch (err) {
      setPathError(err instanceof Error ? err.message : "Failed to reset path");
    } finally {
      setPathSaving(null);
    }
  }, [refreshAccessHealth]);

  const addForbiddenUrl = useCallback(async () => {
    const url = newForbiddenUrl.trim();
    if (!url) return;
    setForbiddenSaving(true);
    try {
      const data = (await engine.post("/settings/forbidden-urls", { url })) as {
        urls?: string[];
      };
      setForbiddenUrls(data?.urls ?? []);
      setNewForbiddenUrl("");
    } catch {
      /* ignore */
    } finally {
      setForbiddenSaving(false);
    }
  }, [newForbiddenUrl]);

  const removeForbiddenUrl = useCallback(async (url: string) => {
    try {
      const encoded = encodeURIComponent(url);
      const data = (await engine.delete(
        `/settings/forbidden-urls/${encoded}`,
      )) as { urls?: string[] };
      setForbiddenUrls(data?.urls ?? []);
    } catch {
      /* ignore */
    }
  }, []);

  // Update state is now managed by the useAutoUpdate hook in App.tsx.
  // Derive convenience variables from the props.
  const updateStatus = updateState?.status ?? null;
  const checking = updateState?.busy ?? false;
  const updateShowDownloadProgress = updateState?.showDownloadProgress ?? false;
  const updateRestarting = updateState?.restarting ?? false;

  const updateSetting = <K extends keyof AppSettings>(
    key: K,
    value: AppSettings[K],
  ) => {
    // Optimistic local state update — instant
    setSettings((prev) => (prev ? { ...prev, [key]: value } : prev));
    // Write to localStorage + sync side-effects (proxy, tunnel, autostart, etc.)
    saveSetting(key, value).then(() => {
      // Fire-and-forget full sync to engine + cloud in the background.
      // Errors are logged by syncAllSettings; they don't block the UI.
      syncAllSettings().catch((err) => {
        console.warn(
          "[Settings] Background sync failed after updateSetting:",
          err,
        );
      });
    });
  };

  const handleRestartEngine = async () => {
    setRestarting(true);
    setRestartError(null);
    try {
      if (isTauri()) {
        const { stopSidecar, startSidecar, waitForEngine, discoverEnginePort } =
          await import("@/lib/sidecar");
        await stopSidecar();
        // Small delay for port release
        await new Promise((r) => setTimeout(r, 500));
        await startSidecar();
        // Wait for engine to actually be ready before refreshing. The engine
        // auto-scans its port range, so prefer the last-known URL and fall
        // back to a port scan if the engine came back on a different port.
        const baseUrl = engineUrl ?? ENGINE_DEFAULT_URL;
        const ready = await waitForEngine(baseUrl, 60, 1000);
        if (!ready) {
          const alt = await discoverEnginePort();
          if (!alt) {
            setRestartError(
              "Engine did not become reachable after restart. Try Reconnect, or check Engine Monitor for logs.",
            );
          }
        }
      }
      await onRefresh();
    } catch (err) {
      setRestartError(
        err instanceof Error ? err.message : "Engine restart failed",
      );
      // Still try to reconnect even if restart failed
      onRefresh();
    } finally {
      setRestarting(false);
    }
  };

  const handleOpenFolder = async (folder: "logs" | "data") => {
    if (engineStatus !== "connected") return;
    try {
      await engine.openSystemFolder(folder);
    } catch {
      // Engine may not be reachable
    }
  };

  const loadCapabilities = useCallback(async () => {
    if (engineStatus !== "connected") return;
    try {
      const result = await engine.getCapabilities();
      setCapabilities(result.capabilities);
    } catch (err) {
      console.error("[Settings] Failed to load capabilities:", err);
    }
  }, [engineStatus]);

  const handleInstallCapability = async (capabilityId: string) => {
    capabilitySseCleanup.current?.();
    capabilitySseCleanup.current = null;
    setInstallingId(capabilityId);
    setInstallProgress((prev) => ({
      ...prev,
      [capabilityId]: { percent: 0, message: "Starting installation…" },
    }));
    setInstallResult((prev) => {
      const next = { ...prev };
      delete next[capabilityId];
      return next;
    });

    const finish = (success: boolean, message: string) => {
      capabilitySseCleanup.current?.();
      capabilitySseCleanup.current = null;
      setInstallResult((prev) => ({
        ...prev,
        [capabilityId]: { success, message },
      }));
      setInstallingId(null);
      if (success) void loadCapabilities();
    };

    try {
      const started = await engine.installCapability(capabilityId);

      if (started.status === "complete") {
        finish(true, started.message || "Installed successfully.");
        return;
      }
      if (started.status === "error") {
        finish(
          false,
          started.error || started.message || "Installation failed.",
        );
        return;
      }

      // Heavy path (Whisper): POST returned immediately — follow SSE for progress.
      if (started.async_install || started.status === "running") {
        setInstallProgress((prev) => ({
          ...prev,
          [capabilityId]: {
            percent: started.percent ?? 0,
            message: started.message || "Downloading…",
          },
        }));

        await new Promise<void>((resolve) => {
          const cleanup = engine.streamCapabilityInstall(capabilityId, (ev) => {
            if (ev.log) {
              setInstallProgress((prev) => ({
                ...prev,
                [capabilityId]: {
                  percent: prev[capabilityId]?.percent ?? 0,
                  message: ev.message || prev[capabilityId]?.message || "",
                },
              }));
              return;
            }
            if (ev.percent !== undefined || ev.message) {
              setInstallProgress((prev) => ({
                ...prev,
                [capabilityId]: {
                  percent: ev.percent ?? prev[capabilityId]?.percent ?? 0,
                  message:
                    ev.message || prev[capabilityId]?.message || "Installing…",
                },
              }));
            }
            if (ev.status === "complete") {
              finish(true, ev.message || "Installed successfully.");
              resolve();
            } else if (ev.status === "error") {
              finish(false, ev.error || ev.message || "Installation failed.");
              resolve();
            }
          });
          capabilitySseCleanup.current = cleanup;
        });
        return;
      }

      // Unexpected residual status after early returns (idle/connected/waiting).
      finish(
        false,
        started.message || `Unexpected install status: ${started.status}`,
      );
    } catch (err) {
      const errMsg = String(err);
      const engineCrashed =
        errMsg.includes("fetch") ||
        errMsg.includes("network") ||
        errMsg.includes("Failed to fetch") ||
        errMsg.includes("Load failed") ||
        errMsg.includes("NetworkError");

      finish(
        false,
        engineCrashed
          ? "The engine became unreachable during installation. It may have been killed by the OS. Open Engine Monitor for logs, then restart the engine."
          : errMsg,
      );
    }
  };

  const loadTunnelStatus = useCallback(async () => {
    if (engineStatus !== "connected") return;
    setTunnelFetching(true);
    try {
      const status = (await engine.get(
        "/tunnel/status",
      )) as typeof tunnelStatus;
      setTunnelStatus(status);
    } catch {
      // Non-critical
    } finally {
      setTunnelFetching(false);
    }
  }, [engineStatus]);

  const loadApiKeyStatus = useCallback(async () => {
    if (engineStatus !== "connected") return;
    try {
      const data = (await engine.get("/settings/api-keys")) as {
        providers: ApiKeyProviderStatus[];
      };
      setApiKeyProviders([...(data?.providers ?? [])]);
    } catch {
      // Non-critical
    }
  }, [engineStatus]);

  /** Ask the engine to check a key against the provider's free auth endpoint.
   *  Costs nothing — these are whoami / model-list calls, never inference.
   *  Pass a `key` to test a typed-but-unsaved value; omit it to test the
   *  stored one. */
  const handleApiKeyTest = useCallback(
    async (provider: string, key?: string): Promise<ApiKeyValidation | null> => {
      setApiKeyTesting((prev) => ({ ...prev, [provider]: true }));
      try {
        const result = (await engine.post(
          `/settings/api-keys/${provider}/validate`,
          key ? { key } : {},
        )) as ApiKeyValidation;
        setApiKeyVerdicts((prev) => ({ ...prev, [provider]: result }));
        return result;
      } catch (err) {
        // The engine itself is unreachable. That says nothing about the key,
        // so it is an `unknown`, never an `invalid`.
        const result: ApiKeyValidation = {
          provider,
          verdict: "unknown",
          message:
            err instanceof Error
              ? `Couldn't run the test: ${err.message}`
              : "Couldn't run the test.",
          account: null,
          status_code: null,
        };
        setApiKeyVerdicts((prev) => ({ ...prev, [provider]: result }));
        return result;
      } finally {
        setApiKeyTesting((prev) => ({ ...prev, [provider]: false }));
      }
    },
    [],
  );

  const handleTestAllKeys = useCallback(async () => {
    setTestingAllKeys(true);
    try {
      const data = (await engine.post(
        "/settings/api-keys/validate-all",
        {},
      )) as { results: ApiKeyValidation[] };
      setApiKeyVerdicts((prev) => {
        const next = { ...prev };
        for (const r of data?.results ?? []) next[r.provider] = r;
        return next;
      });
    } catch {
      // Per-row verdicts are unchanged; the user can retry individually.
    } finally {
      setTestingAllKeys(false);
    }
  }, []);

  const handleApiKeySave = useCallback(
    async (provider: string) => {
      const key = apiKeyInputs[provider]?.trim();
      if (!key) return;
      setApiKeySaving((prev) => ({ ...prev, [provider]: true }));
      setApiKeyMessages((prev) => ({
        ...prev,
        [provider]: undefined as never,
      }));
      try {
        await engine.put(`/settings/api-keys/${provider}`, { key });
        setApiKeyProviders((prev) =>
          prev.map((p) =>
            p.provider === provider ? { ...p, configured: true } : p,
          ),
        );
        setApiKeyInputs((prev) => ({ ...prev, [provider]: "" }));
        setApiKeyMessages((prev) => ({
          ...prev,
          [provider]: { ok: true, text: "Key saved" },
        }));
        setTimeout(
          () =>
            setApiKeyMessages((prev) => {
              const next = { ...prev };
              delete next[provider];
              return next;
            }),
          3000,
        );
        // Test the key we just saved. Catching a bad paste at the moment of
        // saving is the whole point — a key that silently doesn't work until a
        // download fails hours later is the failure mode we're removing.
        const validation = await handleApiKeyTest(provider);
        const { actionNeededStore } = await import("@/features/action-needed");
        if (validation?.verdict === "valid" || validation?.verdict === "unsupported") {
          actionNeededStore.resolve(`api-key:${provider}`, Date.now());
          actionNeededStore.resolveMatching(
            (item) => item.action.provider === provider,
          );
        } else if (validation) {
          actionNeededStore.upsert({
            fingerprint: `api-key:${provider}`,
            code:
              validation.verdict === "invalid"
                ? "api_key_invalid"
                : "api_key_unverified",
            kind: "api_key",
            feature: "provider-keys",
            title:
              validation.verdict === "invalid"
                ? "This API key was rejected"
                : "This API key could not be verified",
            message: validation.message,
            action: {
              kind: "navigate",
              label: "Review API key",
              provider,
              route: `/settings?tab=api-keys&provider=${encodeURIComponent(provider)}`,
            },
            source: `api-key-validation:${provider}`,
            status: "active",
            observed_at: Date.now(),
          });
        }
      } catch (err) {
        setApiKeyMessages((prev) => ({
          ...prev,
          [provider]: {
            ok: false,
            text: err instanceof Error ? err.message : "Failed to save",
          },
        }));
      } finally {
        setApiKeySaving((prev) => ({ ...prev, [provider]: false }));
      }
    },
    [apiKeyInputs, handleApiKeyTest],
  );

  const handleApiKeyDelete = useCallback(async (provider: string) => {
    setApiKeyDeleting((prev) => ({ ...prev, [provider]: true }));
    try {
      await engine.delete(`/settings/api-keys/${provider}`);
      setApiKeyProviders((prev) =>
        prev.map((p) =>
          p.provider === provider
            ? {
                ...p,
                configured: false,
                last_verdict: null,
                last_account: null,
                last_checked_at: null,
              }
            : p,
        ),
      );
      // The verdict described a key that no longer exists.
      setApiKeyVerdicts((prev) => {
        const next = { ...prev };
        delete next[provider];
        return next;
      });
      setApiKeyMessages((prev) => ({
        ...prev,
        [provider]: { ok: true, text: "Key removed" },
      }));
      setTimeout(
        () =>
          setApiKeyMessages((prev) => {
            const next = { ...prev };
            delete next[provider];
            return next;
          }),
        3000,
      );
    } catch (err) {
      setApiKeyMessages((prev) => ({
        ...prev,
        [provider]: {
          ok: false,
          text: err instanceof Error ? err.message : "Failed to remove",
        },
      }));
    } finally {
      setApiKeyDeleting((prev) => ({ ...prev, [provider]: false }));
    }
  }, []);

  // Keep the provider patterns fresh from the remote catalog so a new
  // provider row in the DB is recognised without an app update.
  useEffect(() => {
    void refreshApiKeyPatterns();
  }, []);

  const handleBulkEnvChange = useCallback((text: string) => {
    setBulkEnvText(text);
    setBulkResult(null);
    const parsed = parseEnvBlock(text);
    setBulkParsed(parsed);
    setBulkEditedValues({});
    setBulkCustomMapping({});
    setBulkShowValues({});
    // Auto-select all entries that match a known provider
    setBulkSelected(
      new Set(parsed.filter((e) => e.provider !== null).map((e) => e.rawKey)),
    );
  }, []);

  const handleBulkImport = useCallback(async () => {
    // Collect entries to save: matched OR custom-mapped, and selected
    const toSave = bulkParsed.filter((e) => {
      if (!bulkSelected.has(e.rawKey)) return false;
      const effectiveProvider =
        e.provider ?? bulkCustomMapping[e.rawKey] ?? null;
      return effectiveProvider !== null;
    });
    if (toSave.length === 0) return;

    setBulkSaving(true);
    setBulkResult(null);
    try {
      const result = (await engine.post("/settings/api-keys/bulk", {
        keys: toSave.map((e) => ({
          provider: e.provider ?? bulkCustomMapping[e.rawKey],
          key: (bulkEditedValues[e.rawKey] ?? e.rawValue).trim(),
        })),
      })) as {
        saved: string[];
        skipped: string[];
        errors: Record<string, string>;
      };
      setBulkResult(result);
      if (result.saved.length > 0) {
        await loadApiKeyStatus();
        // Verify everything that just landed, so a stale key pasted from an old
        // .env is caught here rather than at the first download.
        void handleTestAllKeys();
      }
      // Do NOT clear the form — let the user see what was saved vs errored
    } catch (err) {
      setBulkResult({
        saved: [],
        skipped: [],
        errors: { _: err instanceof Error ? err.message : String(err) },
      });
    } finally {
      setBulkSaving(false);
    }
  }, [
    bulkParsed,
    bulkSelected,
    bulkEditedValues,
    bulkCustomMapping,
    loadApiKeyStatus,
    handleTestAllKeys,
  ]);

  const handleTunnelToggle = async (enable: boolean) => {
    setTunnelLoading(true);
    try {
      const result = (await engine.post(
        enable ? "/tunnel/start" : "/tunnel/stop",
        {},
      )) as typeof tunnelStatus;
      setTunnelStatus(result);
      // Persist to localStorage directly (without re-triggering the engine call
      // that saveSetting would fire via syncSetting("tunnelEnabled")).
      const current = await loadSettings();
      const updated = { ...current, tunnelEnabled: enable };
      await saveSettings(updated);
      setSettings((prev) => (prev ? { ...prev, tunnelEnabled: enable } : prev));
      broadcastSettingsChanged();
      // Push full settings blob to cloud in the background.
      syncAllSettings().catch((err) => {
        console.warn(
          "[Settings] Background cloud sync failed after tunnel toggle:",
          err,
        );
      });
    } catch (err) {
      console.error("[Settings] Tunnel toggle failed:", err);
    } finally {
      setTunnelLoading(false);
    }
  };

  const handleCopyTunnelUrl = () => {
    if (tunnelStatus?.url) {
      navigator.clipboard.writeText(tunnelStatus.url);
      setTunnelCopied(true);
      setTimeout(() => setTunnelCopied(false), 2000);
    }
  };

  // Proxy handlers
  const handleProxyTest = async () => {
    setProxyTesting(true);
    setProxyTestResult(null);
    try {
      const result = await engine.proxyTest();
      if (result.success) {
        setProxyTestResult(`Connected via ${result.proxy_url}`);
      } else {
        setProxyTestResult(`Failed: ${result.error}`);
      }
    } catch (err) {
      setProxyTestResult(`Error: ${err}`);
    } finally {
      setProxyTesting(false);
      loadProxyStatus();
    }
  };

  // Apply settings pulled from the cloud: merge into localStorage and notify
  // every mounted component. Without this, the pulled blob is discarded —
  // loadSettings() only reads the unchanged localStorage.
  const applyPulledCloudSettings = async (
    cloudSettings: Record<string, unknown>,
  ) => {
    const local = await loadSettings();
    const merged = mergeCloudSettings(local, cloudSettings);
    await saveSettings(merged);
    setSettings(merged);
    broadcastSettingsChanged();
  };

  // Cloud sync handlers
  const handleSync = async () => {
    setSyncing(true);
    setSyncStatus(null);
    try {
      const result = await engine.triggerCloudSync();
      setSyncStatus(
        `Sync ${result.status}${result.reason ? `: ${result.reason}` : ""}`,
      );
      if (result.settings) {
        await applyPulledCloudSettings(result.settings);
      }
    } catch (err) {
      setSyncStatus(`Sync failed: ${err}`);
    } finally {
      setSyncing(false);
    }
  };

  const handlePushToCloud = async () => {
    setSyncing(true);
    setSyncStatus(null);
    try {
      if (settings) {
        await engine.updateCloudSettings(settingsToCloud(settings));
      }
      const result = await engine.pushCloudSettings();
      setSyncStatus(`Push ${result.status}`);
    } catch (err) {
      setSyncStatus(`Push failed: ${err}`);
    } finally {
      setSyncing(false);
    }
  };

  const handlePullFromCloud = async () => {
    setSyncing(true);
    setSyncStatus(null);
    try {
      const result = await engine.pullCloudSettings();
      setSyncStatus(`Pull ${result.status}`);
      if (result.settings) {
        await applyPulledCloudSettings(result.settings);
      }
    } catch (err) {
      setSyncStatus(`Pull failed: ${err}`);
    } finally {
      setSyncing(false);
    }
  };

  const handleCopyProxyUrl = () => {
    if (proxyStatus?.proxy_url) {
      navigator.clipboard.writeText(proxyStatus.proxy_url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  if (!settings) return null;

  const settingsTabs = [
    { value: "general", label: "General" },
    { value: "system", label: "System" },
    { value: "voice-assistant", label: "Voice Assistant" },
    { value: "api-keys", label: "API Keys" },
    { value: "storage", label: "Storage" },
    { value: "proxy", label: "Proxy" },
    { value: "remote", label: "Remote Access" },
    { value: "scraping", label: "Scraping" },
    { value: "capabilities", label: "Capabilities" },
    { value: "cloud", label: "Cloud & Account" },
    { value: "about", label: "About" },
  ];

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PageHeader
        title="Settings"
        description="Configure the desktop application and cloud sync"
      />

      <SubTabBar
        tabs={settingsTabs}
        value={activeTab}
        onValueChange={setActiveTab}
      />

      <ScrollArea className="flex-1">
        <div className="mx-auto max-w-2xl space-y-6 p-6">
          {/* ── General Tab ──────────────────────────────────── */}
          {activeTab === "general" && (
            <>
              {/* Engine Connection */}
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Server className="h-4 w-4 text-primary" />
                    Engine Connection
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <Label>Status</Label>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        Current engine connection state
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge
                        variant={
                          engineStatus === "connected" ? "success" : "secondary"
                        }
                      >
                        {engineStatus}
                      </Badge>
                      {engineUrl && (
                        <span className="text-xs font-mono text-muted-foreground">
                          {engineUrl}
                        </span>
                      )}
                    </div>
                  </div>

                  <Separator />

                  <div className="flex items-center justify-between">
                    <div>
                      <Label>Engine Port</Label>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        Port range {ENGINE_PORT_RANGE_LABEL} is scanned automatically
                      </p>
                    </div>
                    <Input
                      value={String(ENGINE_PORT_BASE)}
                      disabled
                      className="w-24 text-right font-mono text-sm"
                    />
                  </div>

                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={onRefresh}
                      className="flex-1"
                    >
                      <RefreshCw className="h-4 w-4" /> Reconnect
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={handleRestartEngine}
                      disabled={restarting}
                      className="flex-1"
                    >
                      <Power className="h-4 w-4" />{" "}
                      {restarting ? "Restarting..." : "Restart Engine"}
                    </Button>
                  </div>
                  {restartError && (
                    <p className="text-xs text-red-400">
                      <AlertCircle className="mr-1 inline h-3.5 w-3.5" />
                      {restartError}
                    </p>
                  )}
                </CardContent>
              </Card>

              {/* Application Settings */}
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Palette className="h-4 w-4 text-primary" /> Application
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <Label>Theme</Label>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        Choose the application color scheme
                      </p>
                    </div>
                    <Select
                      value={theme}
                      onValueChange={(v) => {
                        setTheme(v as Theme);
                        updateSetting("theme", v as AppSettings["theme"]);
                      }}
                    >
                      <SelectTrigger className="w-32">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="dark">Dark</SelectItem>
                        <SelectItem value="light">Light</SelectItem>
                        <SelectItem value="system">System</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>

                  <Separator />

                  <div className="flex items-center justify-between">
                    <div>
                      <Label htmlFor="startup">Launch on Startup</Label>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        Start AI Matrx when you log in
                      </p>
                    </div>
                    <Switch
                      id="startup"
                      checked={settings.launchOnStartup}
                      onCheckedChange={(v) =>
                        updateSetting("launchOnStartup", v)
                      }
                    />
                  </div>

                  <Separator />

                  <div className="flex items-center justify-between">
                    <div>
                      <Label htmlFor="tray">Minimize to Tray</Label>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        Keep running in system tray when window is closed
                      </p>
                    </div>
                    <Switch
                      id="tray"
                      checked={settings.minimizeToTray}
                      onCheckedChange={(v) =>
                        updateSetting("minimizeToTray", v)
                      }
                    />
                  </div>

                  <Separator />

                  {/* ── Wake Word / Listen Mode ───────────────────── */}
                  <div>
                    <p className="text-sm font-medium mb-3">
                      Wake Word / Listen Mode
                    </p>
                    <div className="space-y-4">
                      <div className="flex items-center justify-between">
                        <div>
                          <Label htmlFor="ww-enabled">Enable Listen Mode</Label>
                          <p className="text-xs text-muted-foreground mt-0.5">
                            Master switch — turn off to disable wake-word
                            detection entirely
                          </p>
                        </div>
                        <Switch
                          id="ww-enabled"
                          checked={settings.wakeWordEnabled}
                          onCheckedChange={(v) =>
                            updateSetting("wakeWordEnabled", v)
                          }
                        />
                      </div>
                      {settings.wakeWordEnabled && (
                        <div className="flex items-center justify-between">
                          <div>
                            <Label htmlFor="ww-startup">
                              Listen on Startup
                            </Label>
                            <p className="text-xs text-muted-foreground mt-0.5">
                              Automatically enter listen mode when the app
                              launches
                            </p>
                          </div>
                          <Switch
                            id="ww-startup"
                            checked={settings.wakeWordListenOnStartup}
                            onCheckedChange={(v) =>
                              updateSetting("wakeWordListenOnStartup", v)
                            }
                          />
                        </div>
                      )}
                    </div>
                  </div>

                  <Separator />

                  {/* ── Notification Preferences ─────────────────── */}
                  <div>
                    <p className="text-sm font-medium mb-3">
                      Notification Preferences
                    </p>
                    <div className="space-y-4">
                      <div className="flex items-center justify-between">
                        <div>
                          <Label htmlFor="notif-sound">
                            Sound Notifications
                          </Label>
                          <p className="text-xs text-muted-foreground mt-0.5">
                            Play a sound when a notification arrives — important
                            for long-running tasks
                          </p>
                        </div>
                        <Switch
                          id="notif-sound"
                          checked={settings.notificationSound}
                          onCheckedChange={(v) =>
                            updateSetting("notificationSound", v)
                          }
                        />
                      </div>

                      {settings.notificationSound && (
                        <div className="flex items-center justify-between">
                          <div>
                            <Label>Sound Style</Label>
                            <p className="text-xs text-muted-foreground mt-0.5">
                              Tone used for incoming notifications
                            </p>
                          </div>
                          <Select
                            value={settings.notificationSoundStyle}
                            onValueChange={(v) =>
                              updateSetting(
                                "notificationSoundStyle",
                                v as AppSettings["notificationSoundStyle"],
                              )
                            }
                          >
                            <SelectTrigger className="w-32">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="chime">Chime</SelectItem>
                              <SelectItem value="alert">Alert</SelectItem>
                              <SelectItem value="success">Success</SelectItem>
                              <SelectItem value="error">Error</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                      )}
                    </div>
                  </div>
                </CardContent>
              </Card>
            </>
          )}

          {/* ── System Hardware Tab ──────────────────────────── */}
          {activeTab === "system" && (
            <>
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-sm font-medium">System Hardware</h2>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {hardwareProfile?.detected_at
                      ? `Last detected: ${new Date(hardwareProfile.detected_at).toLocaleString()}`
                      : "Hardware inventory for this machine"}
                  </p>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={refreshHardwareProfile}
                  disabled={hardwareLoading || engineStatus !== "connected"}
                >
                  {hardwareLoading ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <RefreshCw className="h-4 w-4" />
                  )}
                  {hardwareLoading ? "Detecting…" : "Refresh"}
                </Button>
              </div>

              {hardwareError && (
                <Card className="border-destructive/50">
                  <CardContent className="pt-4">
                    <div className="flex items-center gap-2 text-destructive text-sm">
                      <AlertCircle className="h-4 w-4 shrink-0" />
                      <span>{hardwareError}</span>
                    </div>
                  </CardContent>
                </Card>
              )}

              {hardwareLoading && !hardwareProfile && (
                <Card>
                  <CardContent className="pt-6 pb-6 flex items-center justify-center gap-3 text-muted-foreground">
                    <Loader2 className="h-5 w-5 animate-spin" />
                    <span className="text-sm">Scanning hardware…</span>
                  </CardContent>
                </Card>
              )}

              {engineStatus !== "connected" && (
                <Card>
                  <CardContent className="pt-6 pb-6 flex items-center justify-center gap-2 text-muted-foreground">
                    <AlertCircle className="h-4 w-4" />
                    <span className="text-sm">
                      Engine not connected — hardware data unavailable
                    </span>
                  </CardContent>
                </Card>
              )}

              {hardwareProfile && !hardwareProfile.error && (
                <>
                  {/* CPUs */}
                  {hardwareProfile.cpus.length > 0 && (
                    <Card>
                      <CardHeader className="pb-3">
                        <CardTitle className="flex items-center gap-2 text-base">
                          <Cpu className="h-4 w-4 text-primary" />
                          {hardwareProfile.cpus.length === 1
                            ? "Processor"
                            : `Processors (${hardwareProfile.cpus.length})`}
                        </CardTitle>
                      </CardHeader>
                      <CardContent className="space-y-3">
                        {hardwareProfile.cpus.map((cpu, i) => (
                          <div key={i} className="space-y-1">
                            {hardwareProfile.cpus.length > 1 && (
                              <p className="text-xs font-medium text-muted-foreground">
                                CPU {i + 1}
                              </p>
                            )}
                            <p className="text-sm font-medium">{cpu.model}</p>
                            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted-foreground">
                              {cpu.physical_cores != null && (
                                <span>
                                  {cpu.physical_cores} physical cores /{" "}
                                  {cpu.logical_cores} threads
                                </span>
                              )}
                              {cpu.architecture && (
                                <span>Architecture: {cpu.architecture}</span>
                              )}
                              {cpu.frequency_mhz != null && (
                                <span>
                                  {(cpu.frequency_mhz / 1000).toFixed(2)} GHz
                                  {cpu.frequency_max_mhz
                                    ? ` (max ${(cpu.frequency_max_mhz / 1000).toFixed(2)} GHz)`
                                    : ""}
                                </span>
                              )}
                            </div>
                          </div>
                        ))}
                      </CardContent>
                    </Card>
                  )}

                  {/* GPUs */}
                  {hardwareProfile.gpus.length > 0 && (
                    <Card>
                      <CardHeader className="pb-3">
                        <CardTitle className="flex items-center gap-2 text-base">
                          <Layers className="h-4 w-4 text-primary" />
                          {hardwareProfile.gpus.length === 1
                            ? "Graphics"
                            : `Graphics (${hardwareProfile.gpus.length})`}
                        </CardTitle>
                      </CardHeader>
                      <CardContent className="space-y-3">
                        {hardwareProfile.gpus.map((gpu, i) => (
                          <div key={i} className={i > 0 ? "pt-3 border-t" : ""}>
                            <div className="flex items-center justify-between">
                              <p className="text-sm font-medium">{gpu.name}</p>
                              <div className="flex gap-1">
                                {gpu.is_primary && (
                                  <Badge variant="outline" className="text-xs">
                                    Primary
                                  </Badge>
                                )}
                                <Badge
                                  variant={
                                    gpu.backend === "cpu"
                                      ? "secondary"
                                      : "default"
                                  }
                                  className="text-xs capitalize"
                                >
                                  {gpu.backend}
                                </Badge>
                              </div>
                            </div>
                            <div className="mt-1 text-xs text-muted-foreground space-y-0.5">
                              {gpu.vram_mb != null && (
                                <p>
                                  {gpu.vram_note === "unified_memory"
                                    ? `${(gpu.vram_mb / 1024).toFixed(0)} GB unified memory`
                                    : `${(gpu.vram_mb / 1024).toFixed(1)} GB VRAM`}
                                </p>
                              )}
                              {gpu.driver_version && (
                                <p>Driver: {gpu.driver_version}</p>
                              )}
                              {gpu.backend === "cpu" && (
                                <p className="text-amber-500">
                                  No GPU acceleration — CPU inference only
                                </p>
                              )}
                            </div>
                          </div>
                        ))}
                      </CardContent>
                    </Card>
                  )}

                  {/* RAM */}
                  {hardwareProfile.ram &&
                    hardwareProfile.ram.total_mb != null && (
                      <Card>
                        <CardHeader className="pb-3">
                          <CardTitle className="flex items-center gap-2 text-base">
                            <MemoryStick className="h-4 w-4 text-primary" />
                            Memory (RAM)
                          </CardTitle>
                        </CardHeader>
                        <CardContent>
                          <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm">
                            <div>
                              <span className="text-muted-foreground text-xs">
                                Total
                              </span>
                              <p className="font-medium">
                                {(hardwareProfile.ram.total_mb / 1024).toFixed(
                                  1,
                                )}{" "}
                                GB
                              </p>
                            </div>
                            {hardwareProfile.ram.available_mb != null && (
                              <div>
                                <span className="text-muted-foreground text-xs">
                                  Available
                                </span>
                                <p className="font-medium">
                                  {(
                                    hardwareProfile.ram.available_mb / 1024
                                  ).toFixed(1)}{" "}
                                  GB
                                </p>
                              </div>
                            )}
                            {hardwareProfile.ram.type && (
                              <div>
                                <span className="text-muted-foreground text-xs">
                                  Type
                                </span>
                                <p className="font-medium">
                                  {hardwareProfile.ram.type}
                                </p>
                              </div>
                            )}
                            {hardwareProfile.ram.speed_mhz != null && (
                              <div>
                                <span className="text-muted-foreground text-xs">
                                  Speed
                                </span>
                                <p className="font-medium">
                                  {hardwareProfile.ram.speed_mhz} MHz
                                </p>
                              </div>
                            )}
                          </div>
                        </CardContent>
                      </Card>
                    )}

                  {/* Audio Inputs */}
                  {(hardwareProfile.audio_inputs.length > 0 ||
                    hardwareProfile.audio_outputs.length > 0) && (
                    <Card>
                      <CardHeader className="pb-3">
                        <CardTitle className="flex items-center gap-2 text-base">
                          <Mic className="h-4 w-4 text-primary" />
                          Audio Devices
                        </CardTitle>
                      </CardHeader>
                      <CardContent className="space-y-4">
                        {hardwareProfile.audio_inputs.length > 0 && (
                          <div>
                            <p className="text-xs font-medium text-muted-foreground mb-2">
                              INPUT ({hardwareProfile.audio_inputs.length})
                            </p>
                            <div className="space-y-1.5">
                              {hardwareProfile.audio_inputs.map((d, i) => (
                                <div
                                  key={i}
                                  className="flex items-center justify-between text-sm"
                                >
                                  <div className="flex items-center gap-2">
                                    <Mic className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                                    <span className="truncate max-w-[260px]">
                                      {d.name}
                                    </span>
                                  </div>
                                  <span className="text-xs text-muted-foreground shrink-0 ml-2">
                                    {d.channels != null
                                      ? `${d.channels}ch`
                                      : ""}
                                    {d.default_sample_rate
                                      ? ` · ${(d.default_sample_rate / 1000).toFixed(1)}kHz`
                                      : ""}
                                  </span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                        {hardwareProfile.audio_inputs.length > 0 &&
                          hardwareProfile.audio_outputs.length > 0 && (
                            <Separator />
                          )}
                        {hardwareProfile.audio_outputs.length > 0 && (
                          <div>
                            <p className="text-xs font-medium text-muted-foreground mb-2">
                              OUTPUT ({hardwareProfile.audio_outputs.length})
                            </p>
                            <div className="space-y-1.5">
                              {hardwareProfile.audio_outputs.map((d, i) => (
                                <div
                                  key={i}
                                  className="flex items-center justify-between text-sm"
                                >
                                  <div className="flex items-center gap-2">
                                    <Speaker className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                                    <span className="truncate max-w-[260px]">
                                      {d.name}
                                    </span>
                                  </div>
                                  <span className="text-xs text-muted-foreground shrink-0 ml-2">
                                    {d.channels != null
                                      ? `${d.channels}ch`
                                      : ""}
                                    {d.default_sample_rate
                                      ? ` · ${(d.default_sample_rate / 1000).toFixed(1)}kHz`
                                      : ""}
                                  </span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                        {hardwareProfile.audio_inputs.length === 0 &&
                          hardwareProfile.audio_outputs.length === 0 && (
                            <p className="text-sm text-muted-foreground">
                              No audio devices found
                            </p>
                          )}
                      </CardContent>
                    </Card>
                  )}

                  {/* Video capture devices */}
                  <Card>
                    <CardHeader className="pb-3">
                      <CardTitle className="flex items-center gap-2 text-base">
                        <Camera className="h-4 w-4 text-primary" />
                        Camera &amp; Video Capture
                      </CardTitle>
                    </CardHeader>
                    <CardContent>
                      {hardwareProfile.video_devices.length === 0 ? (
                        <p className="text-sm text-muted-foreground">
                          No video capture devices found
                        </p>
                      ) : (
                        <div className="space-y-1.5">
                          {hardwareProfile.video_devices.map((d, i) => (
                            <div
                              key={i}
                              className="flex items-center gap-2 text-sm"
                            >
                              <Camera className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                              <span>{d.name}</span>
                              {d.device && (
                                <span className="text-xs text-muted-foreground font-mono">
                                  {d.device}
                                </span>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                    </CardContent>
                  </Card>

                  {/* Monitors */}
                  {hardwareProfile.monitors.length > 0 && (
                    <Card>
                      <CardHeader className="pb-3">
                        <CardTitle className="flex items-center gap-2 text-base">
                          <Monitor className="h-4 w-4 text-primary" />
                          Displays ({hardwareProfile.monitors.length})
                        </CardTitle>
                      </CardHeader>
                      <CardContent className="space-y-3">
                        {hardwareProfile.monitors.map((m, i) => (
                          <div key={i} className={i > 0 ? "pt-3 border-t" : ""}>
                            <div className="flex items-center justify-between">
                              <p className="text-sm font-medium">{m.name}</p>
                              {m.is_primary && (
                                <Badge variant="outline" className="text-xs">
                                  Primary
                                </Badge>
                              )}
                            </div>
                            <div className="mt-0.5 text-xs text-muted-foreground">
                              {m.width_px != null && m.height_px != null && (
                                <span>
                                  {m.width_px} × {m.height_px}
                                </span>
                              )}
                              {m.refresh_hz != null && (
                                <span> @ {m.refresh_hz} Hz</span>
                              )}
                            </div>
                          </div>
                        ))}
                      </CardContent>
                    </Card>
                  )}

                  {/* Network */}
                  {hardwareProfile.network_adapters.length > 0 && (
                    <Card>
                      <CardHeader className="pb-3">
                        <CardTitle className="flex items-center gap-2 text-base">
                          <Network className="h-4 w-4 text-primary" />
                          Network Adapters (
                          {
                            hardwareProfile.network_adapters.filter(
                              (a) => a.type !== "loopback",
                            ).length
                          }
                          )
                        </CardTitle>
                      </CardHeader>
                      <CardContent className="space-y-3">
                        {hardwareProfile.network_adapters
                          .filter((a) => a.type !== "loopback")
                          .map((a, i) => (
                            <div
                              key={i}
                              className={i > 0 ? "pt-3 border-t" : ""}
                            >
                              <div className="flex items-center justify-between">
                                <div className="flex items-center gap-2">
                                  {a.type === "wifi" ? (
                                    <Wifi className="h-3.5 w-3.5 text-muted-foreground" />
                                  ) : (
                                    <Network className="h-3.5 w-3.5 text-muted-foreground" />
                                  )}
                                  <span className="text-sm font-medium">
                                    {a.name}
                                  </span>
                                </div>
                                <div className="flex items-center gap-1.5">
                                  <Badge
                                    variant="outline"
                                    className="text-xs capitalize"
                                  >
                                    {a.type}
                                  </Badge>
                                  <span
                                    className={`h-2 w-2 rounded-full ${a.is_up ? "bg-green-500" : "bg-muted"}`}
                                  />
                                </div>
                              </div>
                              <div className="mt-0.5 text-xs text-muted-foreground space-y-0.5">
                                {a.ipv4.length > 0 && (
                                  <p>IPv4: {a.ipv4.join(", ")}</p>
                                )}
                                {a.mac && <p>MAC: {a.mac}</p>}
                                {a.speed_mbps != null && a.speed_mbps > 0 && (
                                  <p>
                                    {a.speed_mbps >= 1000
                                      ? `${(a.speed_mbps / 1000).toFixed(0)} Gbps`
                                      : `${a.speed_mbps} Mbps`}
                                  </p>
                                )}
                              </div>
                            </div>
                          ))}
                      </CardContent>
                    </Card>
                  )}

                  {/* Storage */}
                  {hardwareProfile.storage.length > 0 && (
                    <Card>
                      <CardHeader className="pb-3">
                        <CardTitle className="flex items-center gap-2 text-base">
                          <HardDrive className="h-4 w-4 text-primary" />
                          Storage ({hardwareProfile.storage.length} volume
                          {hardwareProfile.storage.length !== 1 ? "s" : ""})
                        </CardTitle>
                      </CardHeader>
                      <CardContent className="space-y-3">
                        {hardwareProfile.storage.map((s, i) => (
                          <div key={i} className={i > 0 ? "pt-3 border-t" : ""}>
                            <div className="flex items-center justify-between">
                              <span className="text-sm font-medium font-mono truncate max-w-[200px]">
                                {s.mountpoint}
                              </span>
                              <div className="flex items-center gap-1.5">
                                {s.disk_type !== "unknown" && (
                                  <Badge
                                    variant="outline"
                                    className="text-xs uppercase"
                                  >
                                    {s.disk_type}
                                  </Badge>
                                )}
                                {s.fstype && (
                                  <span className="text-xs text-muted-foreground">
                                    {s.fstype}
                                  </span>
                                )}
                              </div>
                            </div>
                            <div className="mt-1.5">
                              <div className="flex justify-between text-xs text-muted-foreground mb-1">
                                <span>
                                  {s.used_gb.toFixed(1)} GB used of{" "}
                                  {s.total_gb.toFixed(1)} GB
                                </span>
                                <span>{s.free_gb.toFixed(1)} GB free</span>
                              </div>
                              <div className="h-1.5 rounded-full bg-muted overflow-hidden">
                                <div
                                  className={`h-full rounded-full ${s.percent_used > 90 ? "bg-destructive" : s.percent_used > 75 ? "bg-amber-500" : "bg-primary"}`}
                                  style={{ width: `${s.percent_used}%` }}
                                />
                              </div>
                            </div>
                          </div>
                        ))}
                      </CardContent>
                    </Card>
                  )}
                </>
              )}
            </>
          )}

          {/* ── Voice Assistant Tab ──────────────────────────── */}
          {activeTab === "voice-assistant" && (
            <VoiceAssistantSettingsTab
              settings={settings}
              updateSetting={updateSetting}
            />
          )}

          {/* ── API Keys Tab ─────────────────────────────────── */}
          {activeTab === "api-keys" && (
            <>
              <Card>
                <CardHeader className="pb-3">
                  <div className="flex items-center justify-between gap-2">
                    <CardTitle className="flex items-center gap-2 text-base">
                      <KeyRound className="h-4 w-4 text-primary" />
                      AI Provider API Keys
                    </CardTitle>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={
                        testingAllKeys ||
                        engineStatus !== "connected" ||
                        !apiKeyProviders.some((p) => p.configured && p.testable)
                      }
                      onClick={() => void handleTestAllKeys()}
                      className="shrink-0"
                      title="Check every saved key against its provider. Free — no credits are used."
                    >
                      {testingAllKeys ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <ShieldCheck className="h-3.5 w-3.5" />
                      )}
                      Test All
                    </Button>
                  </div>
                </CardHeader>
                <CardContent className="space-y-2">
                  <p className="text-xs text-muted-foreground">
                    Testing a key is free — it calls the provider's sign-in check
                    (not the model), so it never uses credits.
                  </p>
                  <p className="text-xs text-muted-foreground">
                    Enter your own API keys to use AI providers directly from
                    this device. The Hugging Face entry is also used for local
                    GGUF downloads (including XET-hosted models) and gated image
                    checkpoints; the Civitai entry is used for downloading
                    custom image models and LoRA styles. Keys are stored locally
                    on this machine only and are never sent to AI Matrx servers.
                    Leave a key blank if you don't have one — that provider will
                    be unavailable.
                  </p>
                  <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-600 dark:text-amber-400">
                    Keys are base64-encoded in local storage. Do not enter keys
                    you cannot afford to rotate. A cloud relay (no user keys
                    required) is planned for a future release.
                  </div>
                </CardContent>
              </Card>

              {/* ── Bulk .env import ──────────────────────────── */}
              <Card>
                <CardHeader className="pb-2">
                  <div className="flex items-center justify-between">
                    <CardTitle className="flex items-center gap-2 text-sm font-medium">
                      <FileUp className="h-4 w-4 text-primary" />
                      Bulk Import from .env
                    </CardTitle>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 px-2 text-xs"
                      onClick={() => {
                        const opening = !bulkImportOpen;
                        setBulkImportOpen(opening);
                        if (!opening) {
                          setBulkEnvText("");
                          setBulkParsed([]);
                          setBulkSelected(new Set());
                          setBulkResult(null);
                          setBulkEditedValues({});
                          setBulkCustomMapping({});
                          setBulkShowValues({});
                        }
                      }}
                    >
                      {bulkImportOpen ? "Close" : "Open"}
                    </Button>
                  </div>
                  {!bulkImportOpen && (
                    <p className="text-xs text-muted-foreground mt-1">
                      Paste a .env file — we'll detect and import matching API
                      keys automatically.
                    </p>
                  )}
                </CardHeader>

                {bulkImportOpen && (
                  <CardContent className="pt-0 space-y-4">
                    {/* ── Paste zone ── */}
                    <div className="space-y-1.5">
                      <p className="text-xs text-muted-foreground">
                        Paste any block of{" "}
                        <code className="font-mono bg-muted px-1 rounded">
                          KEY=VALUE
                        </code>{" "}
                        lines. Alternate names like{" "}
                        <code className="font-mono bg-muted px-1 rounded">
                          GEMINI_API_KEY
                        </code>{" "}
                        → Google are auto-detected. You can edit any value or
                        manually map unrecognised keys below.
                      </p>
                      <Textarea
                        value={bulkEnvText}
                        onChange={(e) => handleBulkEnvChange(e.target.value)}
                        placeholder={`OPENAI_API_KEY=sk-...\nGEMINI_API_KEY=AIzaSy...\nHUGGING_FACE_HUB_TOKEN=hf_...\n# Comments and unrecognised lines are ignored`}
                        className="font-mono text-sm min-h-40 resize-y"
                        spellCheck={false}
                      />
                    </div>

                    {/* ── Parsed entries ── */}
                    {bulkParsed.length > 0 &&
                      (() => {
                        const matched = bulkParsed.filter(
                          (e) => e.provider !== null,
                        );
                        const unmatched = bulkParsed.filter(
                          (e) => e.provider === null,
                        );
                        const selectableKeys = [
                          ...matched.map((e) => e.rawKey),
                          ...unmatched
                            .filter((e) => bulkCustomMapping[e.rawKey])
                            .map((e) => e.rawKey),
                        ];
                        const allSelected =
                          selectableKeys.length > 0 &&
                          selectableKeys.every((k) => bulkSelected.has(k));

                        return (
                          <div className="space-y-3">
                            {/* Header row */}
                            <div className="flex items-center justify-between">
                              <p className="text-sm font-medium">
                                {matched.length} of {bulkParsed.length} keys
                                matched
                                {unmatched.length > 0 &&
                                  ` · ${unmatched.length} unrecognised`}
                              </p>
                              {selectableKeys.length > 0 && (
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="h-7 text-xs"
                                  onClick={() =>
                                    setBulkSelected(
                                      allSelected
                                        ? new Set()
                                        : new Set(selectableKeys),
                                    )
                                  }
                                >
                                  {allSelected ? "Deselect All" : "Select All"}
                                </Button>
                              )}
                            </div>

                            {/* ── Matched entries ── */}
                            {matched.length > 0 && (
                              <div className="space-y-2">
                                {matched.map((entry) => {
                                  const selected = bulkSelected.has(
                                    entry.rawKey,
                                  );
                                  const alreadyConfigured =
                                    apiKeyProviders.find(
                                      (p) =>
                                        p.provider === entry.provider &&
                                        p.configured,
                                    );
                                  const savedOk = bulkResult?.saved.includes(
                                    entry.provider!,
                                  );
                                  const saveErr =
                                    bulkResult?.errors[entry.provider!];
                                  const editedVal =
                                    bulkEditedValues[entry.rawKey] ??
                                    entry.rawValue;
                                  const showVal =
                                    bulkShowValues[entry.rawKey] ?? false;

                                  return (
                                    <div
                                      key={entry.rawKey}
                                      className={`rounded-lg border transition-colors ${
                                        savedOk
                                          ? "border-emerald-500/40 bg-emerald-500/5"
                                          : saveErr
                                            ? "border-red-500/40 bg-red-500/5"
                                            : selected
                                              ? "border-primary/40 bg-primary/5"
                                              : "border-border/50 bg-muted/10 opacity-60"
                                      }`}
                                    >
                                      {/* Top row: checkbox + labels + status */}
                                      <div className="flex items-center gap-3 px-4 py-3">
                                        <input
                                          type="checkbox"
                                          checked={selected}
                                          disabled={!!savedOk}
                                          onChange={(e) => {
                                            setBulkSelected((prev) => {
                                              const next = new Set(prev);
                                              if (e.target.checked)
                                                next.add(entry.rawKey);
                                              else next.delete(entry.rawKey);
                                              return next;
                                            });
                                          }}
                                          className="h-4 w-4 accent-primary shrink-0"
                                        />
                                        <div className="flex-1 min-w-0">
                                          <div className="flex items-center gap-2 flex-wrap">
                                            <code className="font-mono text-sm font-medium">
                                              {entry.rawKey}
                                            </code>
                                            <span className="rounded-full bg-primary/15 px-2 py-0.5 text-xs font-medium text-primary">
                                              → {entry.label}
                                            </span>
                                            {alreadyConfigured && !savedOk && (
                                              <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-xs font-medium text-amber-600 dark:text-amber-400">
                                                already configured — will
                                                overwrite
                                              </span>
                                            )}
                                            {savedOk && (
                                              <span className="flex items-center gap-1 text-xs text-emerald-500 font-medium">
                                                <CheckCircle2 className="h-3.5 w-3.5" />{" "}
                                                Saved
                                              </span>
                                            )}
                                            {saveErr && (
                                              <span className="flex items-center gap-1 text-xs text-red-500 font-medium">
                                                <AlertCircle className="h-3.5 w-3.5" />{" "}
                                                {saveErr}
                                              </span>
                                            )}
                                          </div>
                                        </div>
                                      </div>
                                      {/* Value row */}
                                      <div className="flex items-center gap-2 px-4 pb-3">
                                        <div className="relative flex-1">
                                          <Input
                                            type={showVal ? "text" : "password"}
                                            value={editedVal}
                                            onChange={(ev) =>
                                              setBulkEditedValues((prev) => ({
                                                ...prev,
                                                [entry.rawKey]: ev.target.value,
                                              }))
                                            }
                                            className="font-mono text-sm pr-10 h-8"
                                            spellCheck={false}
                                            disabled={!!savedOk}
                                          />
                                          <button
                                            type="button"
                                            onClick={() =>
                                              setBulkShowValues((prev) => ({
                                                ...prev,
                                                [entry.rawKey]: !showVal,
                                              }))
                                            }
                                            className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                                            tabIndex={-1}
                                          >
                                            {showVal ? (
                                              <EyeOff className="h-3.5 w-3.5" />
                                            ) : (
                                              <Eye className="h-3.5 w-3.5" />
                                            )}
                                          </button>
                                        </div>
                                        {editedVal !== entry.rawValue && (
                                          <Button
                                            variant="ghost"
                                            size="sm"
                                            className="h-8 text-xs text-muted-foreground"
                                            onClick={() =>
                                              setBulkEditedValues((prev) => {
                                                const next = { ...prev };
                                                delete next[entry.rawKey];
                                                return next;
                                              })
                                            }
                                          >
                                            <RotateCcw className="h-3 w-3" />{" "}
                                            Reset
                                          </Button>
                                        )}
                                      </div>
                                    </div>
                                  );
                                })}
                              </div>
                            )}

                            {/* ── Unmatched entries ── */}
                            {unmatched.length > 0 && (
                              <div className="space-y-2">
                                <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                                  Unrecognised — map manually to import
                                </p>
                                {unmatched.map((entry) => {
                                  const customProvider =
                                    bulkCustomMapping[entry.rawKey] ?? "";
                                  const selected = bulkSelected.has(
                                    entry.rawKey,
                                  );
                                  const savedOk =
                                    customProvider &&
                                    bulkResult?.saved.includes(customProvider);
                                  const saveErr =
                                    customProvider &&
                                    bulkResult?.errors[customProvider];
                                  const editedVal =
                                    bulkEditedValues[entry.rawKey] ??
                                    entry.rawValue;
                                  const showVal =
                                    bulkShowValues[entry.rawKey] ?? false;

                                  return (
                                    <div
                                      key={entry.rawKey}
                                      className={`rounded-lg border transition-colors ${
                                        savedOk
                                          ? "border-emerald-500/40 bg-emerald-500/5"
                                          : saveErr
                                            ? "border-red-500/40 bg-red-500/5"
                                            : customProvider && selected
                                              ? "border-primary/40 bg-primary/5"
                                              : "border-border/40 bg-muted/10"
                                      }`}
                                    >
                                      <div className="flex items-center gap-3 px-4 py-3">
                                        <input
                                          type="checkbox"
                                          checked={selected && !!customProvider}
                                          disabled={
                                            !customProvider || !!savedOk
                                          }
                                          onChange={(e) => {
                                            if (!customProvider) return;
                                            setBulkSelected((prev) => {
                                              const next = new Set(prev);
                                              if (e.target.checked)
                                                next.add(entry.rawKey);
                                              else next.delete(entry.rawKey);
                                              return next;
                                            });
                                          }}
                                          className="h-4 w-4 accent-primary shrink-0"
                                        />
                                        <div className="flex-1 min-w-0 flex items-center gap-3 flex-wrap">
                                          <code className="font-mono text-sm font-medium text-muted-foreground">
                                            {entry.rawKey}
                                          </code>
                                          <span className="text-xs text-muted-foreground/60">
                                            →
                                          </span>
                                          <Select
                                            value={customProvider}
                                            onValueChange={(v) => {
                                              setBulkCustomMapping((prev) => ({
                                                ...prev,
                                                [entry.rawKey]: v,
                                              }));
                                              if (v) {
                                                setBulkSelected((prev) => {
                                                  const next = new Set(prev);
                                                  next.add(entry.rawKey);
                                                  return next;
                                                });
                                              }
                                            }}
                                          >
                                            <SelectTrigger className="h-7 w-44 text-xs">
                                              <SelectValue placeholder="Map to provider…" />
                                            </SelectTrigger>
                                            <SelectContent>
                                              {apiKeyProviders.map((p) => (
                                                <SelectItem
                                                  key={p.provider}
                                                  value={p.provider}
                                                  className="text-xs"
                                                >
                                                  {p.label}
                                                </SelectItem>
                                              ))}
                                            </SelectContent>
                                          </Select>
                                          {savedOk && (
                                            <span className="flex items-center gap-1 text-xs text-emerald-500 font-medium">
                                              <CheckCircle2 className="h-3.5 w-3.5" />{" "}
                                              Saved
                                            </span>
                                          )}
                                          {saveErr && (
                                            <span className="flex items-center gap-1 text-xs text-red-500 font-medium">
                                              <AlertCircle className="h-3.5 w-3.5" />{" "}
                                              {saveErr}
                                            </span>
                                          )}
                                        </div>
                                      </div>
                                      <div className="flex items-center gap-2 px-4 pb-3">
                                        <div className="relative flex-1">
                                          <Input
                                            type={showVal ? "text" : "password"}
                                            value={editedVal}
                                            onChange={(ev) =>
                                              setBulkEditedValues((prev) => ({
                                                ...prev,
                                                [entry.rawKey]: ev.target.value,
                                              }))
                                            }
                                            className="font-mono text-sm pr-10 h-8"
                                            spellCheck={false}
                                            disabled={!!savedOk}
                                          />
                                          <button
                                            type="button"
                                            onClick={() =>
                                              setBulkShowValues((prev) => ({
                                                ...prev,
                                                [entry.rawKey]: !showVal,
                                              }))
                                            }
                                            className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                                            tabIndex={-1}
                                          >
                                            {showVal ? (
                                              <EyeOff className="h-3.5 w-3.5" />
                                            ) : (
                                              <Eye className="h-3.5 w-3.5" />
                                            )}
                                          </button>
                                        </div>
                                        {editedVal !== entry.rawValue && (
                                          <Button
                                            variant="ghost"
                                            size="sm"
                                            className="h-8 text-xs text-muted-foreground"
                                            onClick={() =>
                                              setBulkEditedValues((prev) => {
                                                const next = { ...prev };
                                                delete next[entry.rawKey];
                                                return next;
                                              })
                                            }
                                          >
                                            <RotateCcw className="h-3 w-3" />{" "}
                                            Reset
                                          </Button>
                                        )}
                                      </div>
                                    </div>
                                  );
                                })}
                              </div>
                            )}

                            {/* ── Top-level error (network / unexpected) ── */}
                            {bulkResult?.errors._ && (
                              <div className="rounded-lg border border-red-500/30 bg-red-500/5 px-4 py-2.5 text-sm text-red-500 flex items-center gap-2">
                                <AlertCircle className="h-4 w-4 shrink-0" />
                                {bulkResult.errors._}
                              </div>
                            )}

                            {/* ── Action bar ── */}
                            <div className="flex items-center gap-3 pt-1">
                              <Button
                                onClick={() => void handleBulkImport()}
                                disabled={
                                  bulkSaving ||
                                  bulkSelected.size === 0 ||
                                  engineStatus !== "connected"
                                }
                              >
                                {bulkSaving ? (
                                  <Loader2 className="h-4 w-4 animate-spin" />
                                ) : (
                                  <FileUp className="h-4 w-4" />
                                )}
                                {bulkSaving
                                  ? "Saving…"
                                  : `Import ${bulkSelected.size > 0 ? `${bulkSelected.size} key${bulkSelected.size !== 1 ? "s" : ""}` : "Selected Keys"}`}
                              </Button>
                              <Button
                                variant="ghost"
                                onClick={() => {
                                  setBulkEnvText("");
                                  setBulkParsed([]);
                                  setBulkSelected(new Set());
                                  setBulkResult(null);
                                  setBulkEditedValues({});
                                  setBulkCustomMapping({});
                                  setBulkShowValues({});
                                }}
                                className="text-muted-foreground"
                              >
                                <X className="h-4 w-4" /> Clear
                              </Button>
                            </div>
                          </div>
                        );
                      })()}

                    {/* Empty state after paste that yielded nothing */}
                    {bulkEnvText.trim().length > 0 &&
                      bulkParsed.length === 0 && (
                        <p className="text-sm text-muted-foreground text-center py-4">
                          No API key patterns detected. Make sure your lines
                          follow{" "}
                          <code className="font-mono bg-muted px-1 rounded text-xs">
                            KEY=VALUE
                          </code>{" "}
                          format.
                        </p>
                      )}
                  </CardContent>
                )}
              </Card>

              {engineStatus !== "connected" ? (
                <Card>
                  <CardContent className="py-8 text-center text-sm text-muted-foreground">
                    Connect the engine to manage API keys.
                  </CardContent>
                </Card>
              ) : apiKeyProviders.length === 0 ? (
                <Card>
                  <CardContent className="py-8 text-center">
                    <Loader2 className="mx-auto h-5 w-5 animate-spin text-muted-foreground" />
                  </CardContent>
                </Card>
              ) : (
                <div className="space-y-3">
                  {apiKeyProviders.map((p) => {
                    const inputVal = apiKeyInputs[p.provider] ?? "";
                    const visible = apiKeyVisible[p.provider] ?? false;
                    const saving = apiKeySaving[p.provider] ?? false;
                    const deleting = apiKeyDeleting[p.provider] ?? false;
                    const testing =
                      (apiKeyTesting[p.provider] ?? false) || testingAllKeys;
                    const msg = apiKeyMessages[p.provider];
                    const canSave = inputVal.trim().length > 0;
                    // This session's verdict wins over the one persisted from a
                    // previous run.
                    const live = apiKeyVerdicts[p.provider];
                    const verdict = live?.verdict ?? p.last_verdict ?? null;
                    const account = live?.account ?? p.last_account ?? null;
                    const style = verdict ? VERDICT_STYLES[verdict] : null;
                    // Test the typed key if there is one, else the stored key.
                    // Nothing to test only when both are absent.
                    const canTest =
                      p.testable && (canSave || p.configured) && !testing;
                    return (
                      <Card
                        key={p.provider}
                        id={`api-key-provider-${p.provider}`}
                        tabIndex={-1}
                        className="focus-visible:ring-2 focus-visible:ring-primary"
                      >
                        <CardContent className="py-4">
                          <div className="flex items-start gap-3">
                            <div className="min-w-0 flex-1 space-y-2">
                              <div className="flex items-center gap-2 flex-wrap">
                                <span className="text-sm font-medium">
                                  {p.label}
                                </span>
                                {p.configured ? (
                                  <Badge variant="success" className="text-xs">
                                    Configured
                                  </Badge>
                                ) : (
                                  <Badge
                                    variant="secondary"
                                    className="text-xs"
                                  >
                                    Not set
                                  </Badge>
                                )}
                                {style && (
                                  <span
                                    className={`flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${style.className}`}
                                    title={live?.message ?? undefined}
                                  >
                                    <style.Icon className="h-3.5 w-3.5" />
                                    {style.label}
                                    {account ? ` · ${account}` : ""}
                                  </span>
                                )}
                              </div>
                              <p className="text-xs text-muted-foreground">
                                {p.description}
                              </p>
                              <div className="flex items-center gap-2">
                                <div className="relative flex-1">
                                  <Input
                                    type={visible ? "text" : "password"}
                                    value={inputVal}
                                    onChange={(e) =>
                                      setApiKeyInputs((prev) => ({
                                        ...prev,
                                        [p.provider]: e.target.value,
                                      }))
                                    }
                                    onKeyDown={(e) => {
                                      if (e.key === "Enter" && canSave)
                                        void handleApiKeySave(p.provider);
                                    }}
                                    placeholder={
                                      p.configured
                                        ? "Enter new key to update"
                                        : "Enter API key"
                                    }
                                    className="pr-9 font-mono text-xs"
                                  />
                                  <button
                                    type="button"
                                    onClick={() =>
                                      setApiKeyVisible((prev) => ({
                                        ...prev,
                                        [p.provider]: !prev[p.provider],
                                      }))
                                    }
                                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                                    tabIndex={-1}
                                  >
                                    {visible ? (
                                      <EyeOff className="h-3.5 w-3.5" />
                                    ) : (
                                      <Eye className="h-3.5 w-3.5" />
                                    )}
                                  </button>
                                </div>
                                <Button
                                  size="sm"
                                  disabled={!canSave || saving}
                                  onClick={() =>
                                    void handleApiKeySave(p.provider)
                                  }
                                  className="shrink-0"
                                >
                                  {saving ? (
                                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                  ) : (
                                    <Check className="h-3.5 w-3.5" />
                                  )}
                                  Save
                                </Button>
                                {p.testable && (
                                  <Button
                                    size="sm"
                                    variant="outline"
                                    disabled={!canTest}
                                    onClick={() =>
                                      void handleApiKeyTest(
                                        p.provider,
                                        canSave ? inputVal.trim() : undefined,
                                      )
                                    }
                                    className="shrink-0"
                                    title={
                                      canSave
                                        ? "Check the key you typed, without saving it"
                                        : "Check the saved key against the provider"
                                    }
                                  >
                                    {testing ? (
                                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                    ) : (
                                      <ShieldCheck className="h-3.5 w-3.5" />
                                    )}
                                    Test
                                  </Button>
                                )}
                                {p.configured && (
                                  <Button
                                    size="sm"
                                    variant="outline"
                                    disabled={deleting}
                                    onClick={() =>
                                      void handleApiKeyDelete(p.provider)
                                    }
                                    className="shrink-0 text-destructive hover:text-destructive"
                                  >
                                    {deleting ? (
                                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                    ) : (
                                      <Trash2 className="h-3.5 w-3.5" />
                                    )}
                                    Remove
                                  </Button>
                                )}
                              </div>
                              {msg && (
                                <p
                                  className={`text-xs ${msg.ok ? "text-green-500" : "text-destructive"}`}
                                >
                                  {msg.text}
                                </p>
                              )}
                              {live && (
                                <p
                                  className={`text-xs ${style?.textClassName ?? "text-muted-foreground"}`}
                                >
                                  {live.message}
                                </p>
                              )}
                            </div>
                          </div>
                        </CardContent>
                      </Card>
                    );
                  })}
                </div>
              )}

              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    Where do I get API keys?
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-1.5 text-xs text-muted-foreground">
                  {[
                    {
                      label: "OpenAI",
                      url: "https://platform.openai.com/api-keys",
                    },
                    {
                      label: "Anthropic",
                      url: "https://console.anthropic.com/settings/keys",
                    },
                    {
                      label: "Google (Gemini)",
                      url: "https://aistudio.google.com/app/apikey",
                    },
                    {
                      label: "Hugging Face",
                      url: "https://huggingface.co/settings/tokens",
                    },
                    {
                      label: "Civitai",
                      url: "https://civitai.com/user/account",
                    },
                    { label: "Groq", url: "https://console.groq.com/keys" },
                    {
                      label: "Together AI",
                      url: "https://api.together.ai/settings/api-keys",
                    },
                    { label: "xAI (Grok)", url: "https://console.x.ai/" },
                    {
                      label: "Cerebras",
                      url: "https://cloud.cerebras.ai/platform",
                    },
                  ].map(({ label, url }) => (
                    <div
                      key={label}
                      className="flex items-center justify-between"
                    >
                      <span>{label}</span>
                      <a
                        href={url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center gap-1 text-primary hover:underline"
                      >
                        Get key <ExternalLink className="h-3 w-3" />
                      </a>
                    </div>
                  ))}
                </CardContent>
              </Card>
            </>
          )}

          {/* ── Storage Tab ────────────────────────────────── */}
          {activeTab === "storage" &&
            (() => {
              const userPaths = storagePaths.filter((p) => p.user_visible);
              const internalPaths = storagePaths.filter((p) => !p.user_visible);

              const formatBytes = (bytes: number) => {
                if (bytes === 0) return "0 B";
                const k = 1024;
                const sizes = ["B", "KB", "MB", "GB"];
                const i = Math.floor(Math.log(bytes) / Math.log(k));
                return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
              };

              const StatsCell = ({ name }: { name: string }) => {
                const stats = pathStats[name];
                const loading = statsLoading[name];
                if (loading)
                  return (
                    <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                  );
                // Header copy promises "Click the file count to refresh stats"
                // — so every state of this cell must actually be clickable.
                return (
                  <button
                    type="button"
                    title="Refresh stats"
                    className="cursor-pointer hover:underline disabled:cursor-default disabled:no-underline"
                    disabled={engineStatus !== "connected"}
                    onClick={() => void loadPathStats([name])}
                  >
                    {!stats ? (
                      <span className="text-muted-foreground/40">—</span>
                    ) : !stats.exists ? (
                      <span className="text-muted-foreground/40 text-xs">
                        not found
                      </span>
                    ) : (
                      <span className="text-sm tabular-nums">
                        {stats.file_count.toLocaleString()} file
                        {stats.file_count !== 1 ? "s" : ""}
                        <span className="text-muted-foreground ml-1.5">
                          ({formatBytes(stats.size_bytes)})
                        </span>
                      </span>
                    )}
                  </button>
                );
              };

              const PathRow = ({
                p,
                editable,
              }: {
                p: StoragePath;
                editable: boolean;
              }) => {
                const isEditing = pathEditing === p.name;
                return (
                  <tr
                    key={p.name}
                    className="group border-b last:border-0 hover:bg-muted/30 transition-colors"
                  >
                    {/* Location name */}
                    <td className="py-3 pl-4 pr-3 align-top w-36">
                      <div className="flex flex-col gap-1">
                        <span className="text-sm font-medium leading-tight">
                          {p.label}
                        </span>
                        {p.is_custom ? (
                          <Badge
                            variant="secondary"
                            className="w-fit text-[10px] px-1.5 py-0"
                          >
                            Custom
                          </Badge>
                        ) : (
                          <span className="text-[10px] text-muted-foreground/50">
                            Default
                          </span>
                        )}
                      </div>
                    </td>

                    {/* Path */}
                    <td className="py-3 px-3 align-middle">
                      {isEditing ? (
                        <div className="flex gap-2">
                          <Input
                            value={pathEditValue}
                            onChange={(e) => setPathEditValue(e.target.value)}
                            placeholder={p.default}
                            className="h-8 font-mono text-sm flex-1"
                            autoFocus
                            onKeyDown={(e) => {
                              if (e.key === "Enter") savePathEdit(p.name);
                              if (e.key === "Escape") cancelEditPath();
                            }}
                          />
                          <Button
                            size="sm"
                            className="h-8 px-3 shrink-0"
                            disabled={
                              pathSaving === p.name || !pathEditValue.trim()
                            }
                            onClick={() => savePathEdit(p.name)}
                          >
                            {pathSaving === p.name ? (
                              <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            ) : (
                              <Check className="h-3.5 w-3.5" />
                            )}
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-8 px-2 shrink-0"
                            onClick={cancelEditPath}
                          >
                            <X className="h-3.5 w-3.5" />
                          </Button>
                        </div>
                      ) : (
                        <code className="block font-mono text-sm text-muted-foreground break-all leading-relaxed">
                          {p.current}
                        </code>
                      )}
                    </td>

                    {/* Stats */}
                    <td className="py-3 px-3 align-middle text-right whitespace-nowrap">
                      <StatsCell name={p.name} />
                    </td>

                    {/* Actions */}
                    <td className="py-3 pr-4 pl-2 align-middle whitespace-nowrap">
                      <div className="flex items-center justify-end gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                        {editable && !isEditing && (
                          <>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-7 w-7 p-0"
                              title="Edit path"
                              onClick={() => startEditPath(p)}
                            >
                              <Pencil className="h-3.5 w-3.5" />
                            </Button>
                            {p.is_custom && (
                              <Button
                                variant="ghost"
                                size="sm"
                                className="h-7 w-7 p-0 text-muted-foreground"
                                title="Reset to default"
                                disabled={pathSaving === p.name}
                                onClick={() => resetPathToDefault(p.name)}
                              >
                                {pathSaving === p.name ? (
                                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                ) : (
                                  <RotateCcw className="h-3.5 w-3.5" />
                                )}
                              </Button>
                            )}
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              };

              return (
                <>
                  <FilesystemIndexSettings connected={engineStatus === "connected"} />

                  {/* ── User-visible paths table ── */}
                  <Card>
                    <CardHeader className="pb-2">
                      <div className="flex items-center justify-between">
                        <div>
                          <CardTitle className="flex items-center gap-2 text-base">
                            <HardDrive className="h-4 w-4 text-primary" />{" "}
                            Storage Locations
                          </CardTitle>
                          <p className="text-xs text-muted-foreground mt-1">
                            Customise where Matrx stores your files. Changes
                            take effect immediately. Hover a row to edit. Click
                            the file count to refresh stats.
                          </p>
                        </div>
                        <div className="flex items-center gap-2">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => {
                              void loadStoragePaths();
                              void loadPathStats(
                                storagePaths.map((p) => p.name),
                              );
                            }}
                            disabled={engineStatus !== "connected"}
                          >
                            <RefreshCw className="h-3.5 w-3.5" /> Refresh
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() =>
                              void loadPathStats(userPaths.map((p) => p.name))
                            }
                            disabled={engineStatus !== "connected"}
                          >
                            <Layers className="h-3.5 w-3.5" /> Scan Files
                          </Button>
                        </div>
                      </div>
                    </CardHeader>

                    <CardContent className="p-0">
                      {engineStatus !== "connected" ? (
                        <p className="px-4 py-6 text-sm text-muted-foreground text-center">
                          Connect to the engine to manage storage paths.
                        </p>
                      ) : storagePaths.length === 0 ? (
                        <div className="flex items-center justify-center py-10">
                          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                        </div>
                      ) : (
                        <>
                          {pathError && (
                            <div className="mx-4 mb-3 rounded-lg border border-red-500/30 bg-red-500/5 p-3 text-sm text-red-400">
                              <AlertCircle className="mr-1.5 inline h-4 w-4" />
                              {pathError}
                            </div>
                          )}
                          <table className="w-full text-sm">
                            <thead>
                              <tr className="border-b bg-muted/30">
                                <th className="py-2 pl-4 pr-3 text-left text-xs font-medium text-muted-foreground w-36">
                                  Location
                                </th>
                                <th className="py-2 px-3 text-left text-xs font-medium text-muted-foreground">
                                  Path
                                </th>
                                <th className="py-2 px-3 text-right text-xs font-medium text-muted-foreground">
                                  Contents
                                </th>
                                <th className="py-2 pr-4 pl-2 w-20" />
                              </tr>
                            </thead>
                            <tbody>
                              {userPaths.map((p) => (
                                <PathRow key={p.name} p={p} editable={true} />
                              ))}
                            </tbody>
                          </table>
                        </>
                      )}
                    </CardContent>
                  </Card>

                  {/* ── Internal paths table ── */}
                  {internalPaths.length > 0 && (
                    <Card>
                      <CardHeader className="pb-2">
                        <CardTitle className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                          <HardDrive className="h-4 w-4" /> Internal Directories
                        </CardTitle>
                        <p className="text-xs text-muted-foreground mt-0.5">
                          Engine internals — read-only here. These paths follow
                          OS conventions and change only on reinstall.
                        </p>
                      </CardHeader>
                      <CardContent className="p-0">
                        <table className="w-full text-sm">
                          <thead>
                            <tr className="border-b bg-muted/30">
                              <th className="py-2 pl-4 pr-3 text-left text-xs font-medium text-muted-foreground w-36">
                                Directory
                              </th>
                              <th className="py-2 px-3 text-left text-xs font-medium text-muted-foreground">
                                Path
                              </th>
                              <th className="py-2 px-3 text-right text-xs font-medium text-muted-foreground">
                                Contents
                              </th>
                              <th className="py-2 pr-4 pl-2 w-20" />
                            </tr>
                          </thead>
                          <tbody>
                            {internalPaths.map((p) => (
                              <PathRow key={p.name} p={p} editable={false} />
                            ))}
                          </tbody>
                        </table>
                        <div className="px-4 py-3 border-t">
                          <Button
                            variant="ghost"
                            size="sm"
                            className="text-xs text-muted-foreground"
                            onClick={() =>
                              void loadPathStats(
                                internalPaths.map((p) => p.name),
                              )
                            }
                            disabled={engineStatus !== "connected"}
                          >
                            <Layers className="h-3.5 w-3.5" /> Scan Internal
                            Directories
                          </Button>
                        </div>
                      </CardContent>
                    </Card>
                  )}
                </>
              );
            })()}

          {/* ── Proxy Tab ────────────────────────────────────── */}
          {activeTab === "proxy" && (
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Shield className="h-4 w-4 text-primary" /> Local Proxy
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="flex items-center justify-between">
                  <div>
                    <Label htmlFor="proxy-enabled">Enable Proxy Server</Label>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      Allow your computer to be used as an HTTP proxy for AI
                      Matrx cloud services
                    </p>
                  </div>
                  <Switch
                    id="proxy-enabled"
                    checked={settings.proxyEnabled}
                    onCheckedChange={(v) => updateSetting("proxyEnabled", v)}
                  />
                </div>

                <Separator />

                <div className="flex items-center justify-between">
                  <div>
                    <Label>Proxy Status</Label>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      Current state of the local HTTP proxy
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge
                      variant={proxyStatus?.running ? "success" : "secondary"}
                    >
                      {proxyStatus?.running ? "Running" : "Stopped"}
                    </Badge>
                    {proxyStatus?.running && (
                      <span className="text-xs font-mono text-muted-foreground">
                        :{proxyStatus.port}
                      </span>
                    )}
                  </div>
                </div>

                {proxyStatus?.running && (
                  <>
                    <Separator />
                    <div className="flex items-center justify-between">
                      <div>
                        <Label>Proxy URL</Label>
                        <p className="text-xs text-muted-foreground mt-0.5">
                          Use this URL to route traffic through your machine
                        </p>
                      </div>
                      <div className="flex items-center gap-1">
                        <code className="rounded bg-muted px-2 py-1 text-xs font-mono">
                          {proxyStatus.proxy_url}
                        </code>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 w-7 p-0"
                          onClick={handleCopyProxyUrl}
                        >
                          {copied ? (
                            <CheckCheck className="h-3.5 w-3.5 text-emerald-500" />
                          ) : (
                            <Copy className="h-3.5 w-3.5" />
                          )}
                        </Button>
                      </div>
                    </div>

                    <div className="grid grid-cols-3 gap-3 text-center">
                      <div className="rounded-lg bg-muted/50 p-2">
                        <p className="text-lg font-semibold">
                          {proxyStatus.request_count}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          Requests
                        </p>
                      </div>
                      <div className="rounded-lg bg-muted/50 p-2">
                        <p className="text-lg font-semibold">
                          {proxyStatus.bytes_forwarded > 1048576
                            ? `${(proxyStatus.bytes_forwarded / 1048576).toFixed(1)}MB`
                            : proxyStatus.bytes_forwarded > 1024
                              ? `${(proxyStatus.bytes_forwarded / 1024).toFixed(1)}KB`
                              : `${proxyStatus.bytes_forwarded}B`}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          Forwarded
                        </p>
                      </div>
                      <div className="rounded-lg bg-muted/50 p-2">
                        <p className="text-lg font-semibold">
                          {proxyStatus.active_connections}
                        </p>
                        <p className="text-xs text-muted-foreground">Active</p>
                      </div>
                    </div>
                  </>
                )}

                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    className="flex-1"
                    onClick={handleProxyTest}
                    disabled={proxyTesting || !proxyStatus?.running}
                  >
                    {proxyTesting ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Wifi className="h-4 w-4" />
                    )}
                    Test Connection
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="flex-1"
                    onClick={loadProxyStatus}
                  >
                    <RefreshCw className="h-4 w-4" /> Refresh Status
                  </Button>
                </div>

                {proxyTestResult && (
                  <div
                    className={`rounded-lg border p-3 text-sm ${
                      proxyTestResult.startsWith("Connected")
                        ? "border-emerald-500/30 bg-emerald-500/5 text-emerald-400"
                        : "border-red-500/30 bg-red-500/5 text-red-400"
                    }`}
                  >
                    {proxyTestResult.startsWith("Connected") ? (
                      <CheckCircle2 className="mr-1.5 inline h-4 w-4" />
                    ) : (
                      <AlertCircle className="mr-1.5 inline h-4 w-4" />
                    )}
                    {proxyTestResult}
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          {/* ── Remote Access Tab ────────────────────────────── */}
          {activeTab === "remote" && (
            <>
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Radio className="h-4 w-4 text-primary" /> Remote Access
                  </CardTitle>
                  <p className="text-xs text-muted-foreground mt-1">
                    Open this engine to the internet so you can connect from
                    your phone, tablet, or any browser — without port forwarding
                    or a static IP. Powered by Cloudflare Tunnel.
                  </p>
                </CardHeader>
                <CardContent className="space-y-4">
                  {engineStatus !== "connected" && (
                    <p className="text-xs text-muted-foreground">
                      Connect to the engine to manage remote access.
                    </p>
                  )}

                  <div className="flex items-center justify-between">
                    <div>
                      <Label htmlFor="tunnel-enabled">
                        Enable Remote Access
                      </Label>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        Start a secure tunnel so remote devices can connect to
                        this engine
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      {(tunnelLoading || tunnelFetching) && (
                        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                      )}
                      <Switch
                        id="tunnel-enabled"
                        checked={
                          // While a live status fetch is in-flight, show the persisted
                          // preference so the switch never flickers to OFF on tab return.
                          tunnelFetching
                            ? (settings?.tunnelEnabled ?? false)
                            : (tunnelStatus?.running ??
                              settings?.tunnelEnabled ??
                              false)
                        }
                        disabled={
                          tunnelLoading ||
                          tunnelFetching ||
                          engineStatus !== "connected"
                        }
                        onCheckedChange={handleTunnelToggle}
                      />
                    </div>
                  </div>

                  <Separator />

                  <div className="flex items-center justify-between">
                    <div>
                      <Label>Tunnel Status</Label>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        {tunnelFetching
                          ? "Checking tunnel status…"
                          : tunnelStatus?.running
                            ? `Active · ${tunnelStatus.mode === "named" ? "Named tunnel (stable URL)" : "Quick tunnel (URL changes on restart)"}`
                            : "Tunnel is not running"}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge
                        variant={
                          tunnelStatus?.running
                            ? "success"
                            : tunnelFetching
                              ? "outline"
                              : "secondary"
                        }
                      >
                        {tunnelFetching
                          ? "Checking…"
                          : tunnelStatus?.running
                            ? "Running"
                            : "Stopped"}
                      </Badge>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 w-7 p-0"
                        onClick={loadTunnelStatus}
                        disabled={tunnelFetching}
                      >
                        <RefreshCw
                          className={`h-3.5 w-3.5 ${tunnelFetching ? "animate-spin" : ""}`}
                        />
                      </Button>
                    </div>
                  </div>

                  {tunnelStatus?.running && tunnelStatus.url && (
                    <>
                      <Separator />

                      <div className="space-y-2">
                        <Label>Public URL</Label>
                        <p className="text-xs text-muted-foreground">
                          Share this URL with any authorized device to connect
                          remotely
                        </p>
                        <div className="flex items-center gap-2 rounded-lg border bg-muted/50 px-3 py-2">
                          <Link className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                          <code className="flex-1 truncate text-xs font-mono text-foreground">
                            {tunnelStatus.url}
                          </code>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 w-7 p-0 shrink-0"
                            onClick={handleCopyTunnelUrl}
                            title="Copy URL"
                          >
                            {tunnelCopied ? (
                              <CheckCheck className="h-3.5 w-3.5 text-emerald-500" />
                            ) : (
                              <Copy className="h-3.5 w-3.5" />
                            )}
                          </Button>
                          <a
                            href={tunnelStatus.url}
                            target="_blank"
                            rel="noreferrer"
                            className="text-muted-foreground hover:text-foreground"
                            title="Open in browser"
                          >
                            <ExternalLink className="h-3.5 w-3.5" />
                          </a>
                        </div>
                      </div>

                      <div className="space-y-2">
                        <Label>WebSocket URL</Label>
                        <p className="text-xs text-muted-foreground">
                          Use this in your mobile app or custom client
                        </p>
                        <div className="flex items-center gap-2 rounded-lg border bg-muted/50 px-3 py-2">
                          <code className="flex-1 truncate text-xs font-mono text-muted-foreground">
                            {tunnelStatus.ws_url}
                          </code>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 w-7 p-0 shrink-0"
                            onClick={() => {
                              if (tunnelStatus.ws_url) {
                                navigator.clipboard.writeText(
                                  tunnelStatus.ws_url,
                                );
                              }
                            }}
                            title="Copy WebSocket URL"
                          >
                            <Copy className="h-3.5 w-3.5" />
                          </Button>
                        </div>
                      </div>

                      <div className="flex items-center justify-between rounded-lg bg-muted/50 px-3 py-2 text-xs">
                        <div className="flex items-center gap-1.5 text-muted-foreground">
                          <QrCode className="h-3.5 w-3.5" />
                          <span>Scan on mobile to connect instantly</span>
                        </div>
                        <span className="font-mono text-muted-foreground">
                          {tunnelStatus.uptime_seconds > 3600
                            ? `${Math.floor(tunnelStatus.uptime_seconds / 3600)}h up`
                            : tunnelStatus.uptime_seconds > 60
                              ? `${Math.floor(tunnelStatus.uptime_seconds / 60)}m up`
                              : `${Math.floor(tunnelStatus.uptime_seconds)}s up`}
                        </span>
                      </div>
                    </>
                  )}

                  {!tunnelFetching && !tunnelStatus?.running && (
                    <div className="rounded-lg border border-muted bg-muted/20 p-3 text-xs text-muted-foreground space-y-1">
                      <p className="font-medium text-foreground/70">
                        How it works
                      </p>
                      <p>
                        Enabling remote access starts a secure outbound tunnel
                        via Cloudflare. Each installation gets its own unique
                        URL — no port forwarding, no firewall changes, works on
                        every network.
                      </p>
                      <p>
                        The URL is a random{" "}
                        <code className="font-mono bg-muted px-1 rounded">
                          *.trycloudflare.com
                        </code>{" "}
                        address that is saved to your account so your phone and
                        other devices can always find this PC automatically.
                      </p>
                    </div>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Monitor className="h-4 w-4 text-primary" /> Connected
                    Devices
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 w-6 p-0 ml-auto"
                      onClick={loadInstanceInfo}
                      title="Refresh devices"
                    >
                      <RefreshCw className="h-3.5 w-3.5" />
                    </Button>
                  </CardTitle>
                  <p className="text-xs text-muted-foreground mt-1">
                    Devices linked to your account that can connect remotely.
                    Active tunnel URLs are stored in the cloud and visible to
                    your mobile app.
                  </p>
                </CardHeader>
                <CardContent>
                  {instances.length === 0 ? (
                    <p className="text-xs text-muted-foreground py-2">
                      No registered devices found. Sign in and configure cloud
                      sync to register this device.
                    </p>
                  ) : (
                    <div className="space-y-2">
                      {instances.map((inst) => {
                        const isThis =
                          inst.instance_id === instanceInfo?.instance_id;
                        const hasTunnel =
                          !!inst.tunnel_active && !!inst.tunnel_url;
                        return (
                          <div
                            key={inst.instance_id}
                            className="rounded-lg bg-muted/50 px-3 py-2.5 space-y-1.5"
                          >
                            <div className="flex items-center justify-between">
                              <div className="flex items-center gap-3 min-w-0">
                                <Monitor className="h-4 w-4 text-muted-foreground shrink-0" />
                                <div className="min-w-0">
                                  <p className="text-sm font-medium truncate">
                                    {inst.instance_name ||
                                      inst.hostname ||
                                      "Unknown"}
                                  </p>
                                  <p className="text-xs text-muted-foreground">
                                    {inst.platform} {inst.architecture}
                                    {inst.last_seen &&
                                      ` · ${new Date(inst.last_seen).toLocaleDateString()}`}
                                  </p>
                                </div>
                              </div>
                              <div className="flex items-center gap-2 shrink-0">
                                {hasTunnel && (
                                  <Badge
                                    variant="success"
                                    className="text-xs gap-1"
                                  >
                                    <Radio className="h-2.5 w-2.5" /> Tunnel
                                    Active
                                  </Badge>
                                )}
                                {isThis && (
                                  <Badge variant="outline" className="text-xs">
                                    This Device
                                  </Badge>
                                )}
                              </div>
                            </div>
                            {hasTunnel && (
                              <div className="pl-7 space-y-1">
                                <div className="flex items-center gap-1.5">
                                  <code className="flex-1 truncate text-xs font-mono text-muted-foreground">
                                    {inst.tunnel_url}
                                  </code>
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    className="h-5 w-5 p-0 shrink-0"
                                    onClick={() =>
                                      inst.tunnel_url &&
                                      navigator.clipboard.writeText(
                                        inst.tunnel_url,
                                      )
                                    }
                                    title="Copy REST URL"
                                  >
                                    <Copy className="h-3 w-3" />
                                  </Button>
                                </div>
                                {inst.tunnel_ws_url && (
                                  <div className="flex items-center gap-1.5">
                                    <code className="flex-1 truncate text-xs font-mono text-muted-foreground">
                                      {inst.tunnel_ws_url}
                                    </code>
                                    <Button
                                      variant="ghost"
                                      size="sm"
                                      className="h-5 w-5 p-0 shrink-0"
                                      onClick={() =>
                                        inst.tunnel_ws_url &&
                                        navigator.clipboard.writeText(
                                          inst.tunnel_ws_url!,
                                        )
                                      }
                                      title="Copy WebSocket URL"
                                    >
                                      <Copy className="h-3 w-3" />
                                    </Button>
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Radio className="h-4 w-4 text-primary" /> Extension Bridge
                  </CardTitle>
                  <p className="text-xs text-muted-foreground mt-1">
                    Lets the matrx-extend Chrome extension reach this engine
                    cross-machine via Supabase Broadcast when the local
                    WebSocket bridge isn't available.
                  </p>
                </CardHeader>
                <CardContent>
                  <div className="flex items-center justify-between">
                    <div>
                      <Label htmlFor="extension-broadcast-enabled">
                        Enable Broadcast plumb
                      </Label>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        Default: ON. Disable only if you don't use the extension
                        or want to reduce outbound connections.
                      </p>
                    </div>
                    <Switch
                      id="extension-broadcast-enabled"
                      checked={settings?.extensionBroadcastEnabled ?? true}
                      onCheckedChange={async (checked) => {
                        await saveSetting("extensionBroadcastEnabled", checked);
                        setSettings((prev) =>
                          prev
                            ? { ...prev, extensionBroadcastEnabled: checked }
                            : prev,
                        );
                        broadcastSettingsChanged();
                      }}
                    />
                  </div>
                </CardContent>
              </Card>
            </>
          )}

          {/* ── Scraping Tab ─────────────────────────────────── */}
          {activeTab === "scraping" && (
            <>
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Globe className="h-4 w-4 text-primary" /> Scraping
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <Label htmlFor="headless">Headless Mode</Label>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        Run browser without visible window during scraping
                      </p>
                    </div>
                    <Switch
                      id="headless"
                      checked={settings.headlessScraping}
                      onCheckedChange={(v) =>
                        updateSetting("headlessScraping", v)
                      }
                    />
                  </div>

                  <Separator />

                  <div className="flex items-center justify-between">
                    <div>
                      <Label>Request Delay</Label>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        Minimum seconds between requests to the same domain
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <Input
                        value={settings.scrapeDelay}
                        onChange={(e) =>
                          updateSetting("scrapeDelay", e.target.value)
                        }
                        className="w-20 text-right font-mono text-sm"
                        type="number"
                        min="0.5"
                        max="30"
                        step="0.5"
                      />
                      <span className="text-xs text-muted-foreground">sec</span>
                    </div>
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Ban className="h-4 w-4 text-destructive" /> Forbidden URLs
                  </CardTitle>
                  <p className="text-xs text-muted-foreground mt-1">
                    These domains or patterns are blocked from scraping, even if
                    requested by an AI. Use{" "}
                    <code className="font-mono bg-muted px-1 rounded">
                      *.example.com
                    </code>{" "}
                    to block all subdomains.
                  </p>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div className="flex gap-2">
                    <Input
                      value={newForbiddenUrl}
                      onChange={(e) => setNewForbiddenUrl(e.target.value)}
                      placeholder="example.com or *.ads-tracker.io"
                      className="font-mono text-xs flex-1"
                      onKeyDown={(e) => {
                        if (e.key === "Enter") addForbiddenUrl();
                      }}
                    />
                    <Button
                      size="sm"
                      onClick={addForbiddenUrl}
                      disabled={
                        forbiddenSaving ||
                        !newForbiddenUrl.trim() ||
                        engineStatus !== "connected"
                      }
                      className="gap-1.5 shrink-0"
                    >
                      {forbiddenSaving ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Plus className="h-3.5 w-3.5" />
                      )}
                      Add
                    </Button>
                  </div>

                  {forbiddenUrls.length === 0 ? (
                    <p className="text-xs text-muted-foreground italic py-2">
                      No forbidden URLs configured. All domains are allowed.
                    </p>
                  ) : (
                    <ScrollArea className="max-h-48">
                      <div className="space-y-1">
                        {forbiddenUrls.map((url) => (
                          <div
                            key={url}
                            className="flex items-center gap-2 rounded-lg border border-border/50 bg-muted/20 px-3 py-2"
                          >
                            <Ban className="h-3 w-3 text-destructive/60 shrink-0" />
                            <code className="flex-1 text-xs font-mono text-foreground/80 truncate">
                              {url}
                            </code>
                            <button
                              onClick={() => removeForbiddenUrl(url)}
                              className="text-muted-foreground/40 hover:text-destructive transition-colors shrink-0"
                              title="Remove"
                            >
                              <X className="h-3.5 w-3.5" />
                            </button>
                          </div>
                        ))}
                      </div>
                    </ScrollArea>
                  )}

                  {engineStatus !== "connected" && (
                    <p className="text-xs text-muted-foreground">
                      Connect to the engine to manage forbidden URLs.
                    </p>
                  )}
                </CardContent>
              </Card>
            </>
          )}

          {/* ── Capabilities Tab ─────────────────────────────── */}
          {activeTab === "capabilities" && (
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Cpu className="h-4 w-4 text-primary" /> Capabilities
                </CardTitle>
                <p className="text-xs text-muted-foreground mt-1">
                  Most capabilities are bundled and ready to use. Only large AI
                  models (e.g. Whisper) require separate installation.
                </p>
              </CardHeader>
              <CardContent className="space-y-1 p-0">
                {capabilities.length === 0 && engineStatus !== "connected" && (
                  <div className="px-6 py-8 text-center text-sm text-muted-foreground">
                    Engine not connected — capability status unavailable.
                  </div>
                )}
                {capabilities.length === 0 && engineStatus === "connected" && (
                  <div className="flex items-center justify-center gap-2 px-6 py-8 text-sm text-muted-foreground">
                    <Loader2 className="h-4 w-4 animate-spin" /> Loading...
                  </div>
                )}
                {capabilities.map((cap, idx) => {
                  const isInstalled = cap.status === "installed";
                  const isInstalling = installingId === cap.id;
                  const result = installResult[cap.id];
                  const progress = installProgress[cap.id];
                  return (
                    <div key={cap.id}>
                      {idx > 0 && <Separator />}
                      <div className="px-6 py-4 space-y-2">
                        <div className="flex items-start justify-between gap-4">
                          <div className="flex items-start gap-3 min-w-0">
                            <div className="mt-0.5 shrink-0">
                              {isInstalled ? (
                                <CircleCheck className="h-4 w-4 text-emerald-500" />
                              ) : (
                                <CircleDashed className="h-4 w-4 text-muted-foreground" />
                              )}
                            </div>
                            <div className="min-w-0">
                              <div className="flex items-center gap-2 flex-wrap">
                                <span className="text-sm font-medium">
                                  {cap.name}
                                </span>
                                <Badge
                                  variant={
                                    isInstalled ? "success" : "secondary"
                                  }
                                  className="text-xs"
                                >
                                  {isInstalled ? "Installed" : "Not installed"}
                                </Badge>
                                {cap.size_warning && !isInstalled && (
                                  <Badge
                                    variant="outline"
                                    className="text-xs text-amber-500 border-amber-500/40"
                                  >
                                    {cap.size_warning}
                                  </Badge>
                                )}
                              </div>
                              <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">
                                {cap.description}
                              </p>
                              <div className="flex items-center gap-1 mt-1 flex-wrap">
                                {cap.packages.map((pkg) => (
                                  <code
                                    key={pkg}
                                    className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono"
                                  >
                                    {pkg}
                                  </code>
                                ))}
                              </div>
                            </div>
                          </div>
                          <div className="shrink-0 flex items-center gap-2">
                            {cap.docs_url && (
                              <a
                                href={cap.docs_url}
                                target="_blank"
                                rel="noreferrer"
                                className="text-muted-foreground hover:text-foreground"
                              >
                                <ExternalLink className="h-3.5 w-3.5" />
                              </a>
                            )}
                            {!isInstalled && (
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => handleInstallCapability(cap.id)}
                                disabled={
                                  isInstalling || engineStatus !== "connected"
                                }
                              >
                                {isInstalling ? (
                                  <>
                                    <Loader2 className="h-3.5 w-3.5 animate-spin" />{" "}
                                    Installing...
                                  </>
                                ) : (
                                  <>
                                    <Download className="h-3.5 w-3.5" /> Install
                                  </>
                                )}
                              </Button>
                            )}
                          </div>
                        </div>
                        {isInstalling && progress && (
                          <div className="rounded-md border border-border/60 bg-muted/30 px-3 py-2 space-y-1.5">
                            <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
                              <span className="truncate">
                                {progress.message}
                              </span>
                              <span className="shrink-0 tabular-nums">
                                {Math.round(progress.percent)}%
                              </span>
                            </div>
                            <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                              <div
                                className="h-full rounded-full bg-primary transition-[width] duration-300"
                                style={{
                                  width: `${Math.min(100, Math.max(0, progress.percent))}%`,
                                }}
                              />
                            </div>
                          </div>
                        )}
                        {result && (
                          <div
                            className={`rounded-md border px-3 py-2 text-xs ${
                              result.success
                                ? "border-emerald-500/30 bg-emerald-500/5 text-emerald-400"
                                : "border-red-500/30 bg-red-500/5 text-red-400"
                            }`}
                          >
                            {result.success ? (
                              <CheckCircle2 className="mr-1.5 inline h-3.5 w-3.5" />
                            ) : (
                              <AlertCircle className="mr-1.5 inline h-3.5 w-3.5" />
                            )}
                            {result.message}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
                {capabilities.length > 0 && (
                  <div className="px-6 py-3 border-t">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-xs text-muted-foreground"
                      onClick={loadCapabilities}
                    >
                      <RefreshCw className="h-3 w-3" /> Refresh Status
                    </Button>
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          {/* ── Cloud & Account Tab ──────────────────────────── */}
          {activeTab === "cloud" && (
            <>
              {/* Cloud agent tool exposure (synced, web-controllable) */}
              <CloudAgentToolsCard engineUrl={engineUrl ?? null} />

              {/* Cloud Sync */}
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Cloud className="h-4 w-4 text-primary" /> Cloud Sync
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <Label>Sync Status</Label>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        Settings are synchronized between this device and the
                        cloud
                      </p>
                    </div>
                    <Badge
                      variant={auth.isAuthenticated ? "success" : "secondary"}
                    >
                      {auth.isAuthenticated ? "Connected" : "Not Signed In"}
                    </Badge>
                  </div>

                  {instanceInfo && (
                    <>
                      <Separator />
                      <div className="flex items-center justify-between">
                        <div>
                          <Label>Instance ID</Label>
                          <p className="text-xs text-muted-foreground mt-0.5">
                            Unique identifier for this installation
                          </p>
                        </div>
                        <code className="rounded bg-muted px-2 py-1 text-xs font-mono">
                          {instanceInfo.instance_id.slice(0, 20)}...
                        </code>
                      </div>
                    </>
                  )}

                  {instances.length > 0 && (
                    <>
                      <Separator />
                      <div>
                        <Label>Registered Devices</Label>
                        <p className="text-xs text-muted-foreground mt-0.5 mb-2">
                          All devices linked to your account
                        </p>
                        <div className="space-y-2">
                          {instances.map((inst) => (
                            <div
                              key={inst.instance_id}
                              className="flex items-center justify-between rounded-lg bg-muted/50 px-3 py-2"
                            >
                              <div className="flex items-center gap-2">
                                <Monitor className="h-4 w-4 text-muted-foreground" />
                                <div>
                                  <p className="text-sm font-medium">
                                    {inst.instance_name ||
                                      inst.hostname ||
                                      "Unknown"}
                                  </p>
                                  <p className="text-xs text-muted-foreground">
                                    {inst.platform} {inst.architecture}
                                    {inst.last_seen &&
                                      ` · ${new Date(inst.last_seen).toLocaleDateString()}`}
                                  </p>
                                </div>
                              </div>
                              {inst.instance_id ===
                                instanceInfo?.instance_id && (
                                <Badge variant="outline" className="text-xs">
                                  This Device
                                </Badge>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    </>
                  )}

                  <Separator />

                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      className="flex-1"
                      onClick={handlePushToCloud}
                      disabled={syncing || !auth.isAuthenticated}
                    >
                      {syncing ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <ArrowUpFromLine className="h-4 w-4" />
                      )}
                      Save to Cloud
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      className="flex-1"
                      onClick={handlePullFromCloud}
                      disabled={syncing || !auth.isAuthenticated}
                    >
                      {syncing ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <ArrowDownToLine className="h-4 w-4" />
                      )}
                      Pull from Cloud
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={handleSync}
                      disabled={syncing || !auth.isAuthenticated}
                    >
                      {syncing ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <RefreshCw className="h-4 w-4" />
                      )}
                    </Button>
                  </div>

                  {syncStatus && (
                    <div
                      className={`rounded-lg border p-3 text-sm ${
                        syncStatus.includes("error") ||
                        syncStatus.includes("failed")
                          ? "border-red-500/30 bg-red-500/5 text-red-400"
                          : "border-emerald-500/30 bg-emerald-500/5 text-emerald-400"
                      }`}
                    >
                      {syncStatus.includes("error") ||
                      syncStatus.includes("failed") ? (
                        <CloudOff className="mr-1.5 inline h-4 w-4" />
                      ) : (
                        <Cloud className="mr-1.5 inline h-4 w-4" />
                      )}
                      {syncStatus}
                    </div>
                  )}
                </CardContent>
              </Card>

              {/* Account */}
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Server className="h-4 w-4 text-primary" /> Account
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  {auth.user ? (
                    <>
                      <div className="flex items-center gap-3">
                        <Avatar className="h-10 w-10">
                          <AvatarImage
                            src={auth.user.user_metadata?.avatar_url}
                          />
                          <AvatarFallback>
                            {(auth.user.email?.[0] ?? "U").toUpperCase()}
                          </AvatarFallback>
                        </Avatar>
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-sm font-medium">
                            {auth.user.user_metadata?.full_name ??
                              auth.user.email}
                          </p>
                          <p className="truncate text-xs text-muted-foreground">
                            {auth.user.email}
                          </p>
                        </div>
                        <Badge variant="success" className="shrink-0">
                          {auth.user.app_metadata?.provider ?? "email"}
                        </Badge>
                      </div>
                      <Separator />
                      <Button
                        variant="outline"
                        size="sm"
                        className="w-full"
                        onClick={auth.signOut}
                        disabled={auth.loading}
                      >
                        <Power className="h-4 w-4" /> Sign Out
                      </Button>
                    </>
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      Not signed in
                    </p>
                  )}
                </CardContent>
              </Card>
            </>
          )}

          {/* ── About Tab ────────────────────────────────────── */}
          {activeTab === "about" && (
            <>
              {/* System Info */}
              {instanceInfo && (
                <Card>
                  <CardHeader className="pb-3">
                    <CardTitle className="flex items-center gap-2 text-base">
                      <Monitor className="h-4 w-4 text-primary" /> System
                      Information
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="grid grid-cols-2 gap-y-2 text-sm">
                      {(
                        [
                          ["Platform", instanceInfo.platform],
                          ["OS Version", instanceInfo.os_version],
                          ["Architecture", instanceInfo.architecture],
                          ["Hostname", instanceInfo.hostname],
                          ["CPU", instanceInfo.cpu_model],
                          ["CPU Cores", instanceInfo.cpu_cores?.toString()],
                          [
                            "RAM",
                            instanceInfo.ram_total_gb
                              ? `${instanceInfo.ram_total_gb} GB`
                              : undefined,
                          ],
                          ["Python", instanceInfo.python_version],
                        ] as const
                      )
                        .filter(([, v]) => v)
                        .map(([label, value]) => (
                          <div key={label} className="contents">
                            <span className="text-muted-foreground">
                              {label}
                            </span>
                            <span className="font-mono text-xs">{value}</span>
                          </div>
                        ))}
                    </div>
                  </CardContent>
                </Card>
              )}

              {/* About */}
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <SettingsIcon className="h-4 w-4 text-primary" /> About
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-muted-foreground">
                      Version
                    </span>
                    <Badge variant="secondary">{__APP_VERSION__}</Badge>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-muted-foreground">
                      Engine Version
                    </span>
                    <Badge variant="secondary">
                      {engineVersion || "\u2014"}
                    </Badge>
                  </div>
                  <Separator />

                  {isTauri() && (
                    <>
                      <div className="flex items-center justify-between">
                        <div>
                          <Label>Updates</Label>
                          <p className="text-xs text-muted-foreground mt-0.5">
                            {updateStatus?.status === "installed"
                              ? "Update installed \u2014 restart to apply"
                              : updateShowDownloadProgress &&
                                  updateStatus?.status === "downloading"
                                ? "Downloading update…"
                                : updateStatus?.status === "available" ||
                                    (updateStatus?.status === "downloading" &&
                                      !updateShowDownloadProgress)
                                  ? `v${updateStatus.version} available — preparing in the background; use Install when ready`
                                  : updateStatus?.status === "up_to_date"
                                    ? "You're on the latest version"
                                    : "Check for new releases"}
                          </p>
                        </div>
                        <div className="flex gap-2">
                          {updateStatus?.status === "installed" ? (
                            <Button
                              size="sm"
                              disabled={updateRestarting}
                              onClick={() => void updateActions?.restart()}
                            >
                              {updateRestarting ? (
                                <>
                                  <Loader2 className="h-4 w-4 animate-spin" />
                                  Restarting…
                                </>
                              ) : (
                                <>
                                  <RefreshCw className="h-4 w-4" />
                                  Restart
                                </>
                              )}
                            </Button>
                          ) : updateStatus?.status === "available" ? (
                            <>
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() =>
                                  updateActions?.check({ showResult: true })
                                }
                                disabled={checking}
                              >
                                {checking ? (
                                  <Loader2 className="h-4 w-4 animate-spin" />
                                ) : (
                                  <RefreshCw className="h-4 w-4" />
                                )}
                                Check Again
                              </Button>
                              <Button
                                size="sm"
                                onClick={() => updateActions?.openDialog()}
                                disabled={checking}
                              >
                                {checking ? (
                                  <Loader2 className="h-4 w-4 animate-spin" />
                                ) : (
                                  <Download className="h-4 w-4" />
                                )}
                                Install Update
                              </Button>
                            </>
                          ) : (
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() =>
                                updateActions?.check({ showResult: true })
                              }
                              disabled={checking}
                            >
                              {checking ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                              ) : updateStatus?.status === "up_to_date" ? (
                                <CheckCircle2 className="h-4 w-4" />
                              ) : (
                                <RefreshCw className="h-4 w-4" />
                              )}
                              Check for Updates
                            </Button>
                          )}
                        </div>
                      </div>

                      {settings && (
                        <div className="flex items-center justify-between">
                          <div>
                            <Label htmlFor="auto-check-updates">
                              Automatic Updates
                            </Label>
                            <p className="text-xs text-muted-foreground mt-0.5">
                              Check for updates every{" "}
                              {settings.updateCheckInterval >= 60
                                ? `${Math.round(settings.updateCheckInterval / 60)}h`
                                : `${settings.updateCheckInterval}m`}
                            </p>
                          </div>
                          <Switch
                            id="auto-check-updates"
                            checked={settings.autoCheckUpdates}
                            onCheckedChange={(v) =>
                              updateSetting("autoCheckUpdates", v)
                            }
                          />
                        </div>
                      )}

                      {settings?.autoCheckUpdates && (
                        <div className="flex items-center justify-between">
                          <Label htmlFor="update-interval">
                            Check Interval
                          </Label>
                          <Select
                            value={String(settings.updateCheckInterval)}
                            onValueChange={(v) =>
                              updateSetting("updateCheckInterval", Number(v))
                            }
                          >
                            <SelectTrigger
                              className="w-32"
                              id="update-interval"
                            >
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="60">1 hour</SelectItem>
                              <SelectItem value="240">4 hours</SelectItem>
                              <SelectItem value="720">12 hours</SelectItem>
                              <SelectItem value="1440">24 hours</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                      )}

                      <Separator />
                    </>
                  )}

                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      className="flex-1"
                      onClick={() => handleOpenFolder("logs")}
                      disabled={engineStatus !== "connected"}
                    >
                      <FolderOpen className="h-4 w-4" /> Open Logs Folder
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      className="flex-1"
                      onClick={() => handleOpenFolder("data")}
                      disabled={engineStatus !== "connected"}
                    >
                      <FolderOpen className="h-4 w-4" /> Open Data Folder
                    </Button>
                  </div>
                </CardContent>
              </Card>
            </>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}

// ── Voice Assistant Settings Tab ──────────────────────────────────────────────

interface VoiceAssistantSettingsTabProps {
  settings: AppSettings;
  updateSetting: <K extends keyof AppSettings>(
    key: K,
    value: AppSettings[K],
  ) => void;
}

function VoiceAssistantSettingsTab({
  settings,
  updateSetting,
}: VoiceAssistantSettingsTabProps) {
  const [userPrompts, setUserPrompts] = useState(systemPrompts.list());
  const [newPromptName, setNewPromptName] = useState("");
  const [newPromptContent, setNewPromptContent] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editContent, setEditContent] = useState("");

  const allPrompts = [...builtinPrompts(), ...userPrompts];
  const selectedPromptId =
    settings.voiceAssistantSystemPromptId || "builtin-voice-assistant";
  const selectedPrompt = allPrompts.find((p) => p.id === selectedPromptId);

  const reload = () => setUserPrompts(systemPrompts.list());

  const handleCreate = () => {
    if (!newPromptName.trim() || !newPromptContent.trim()) return;
    systemPrompts.create({
      name: newPromptName.trim(),
      content: newPromptContent.trim(),
      category: "Voice",
    });
    setNewPromptName("");
    setNewPromptContent("");
    reload();
  };

  const handleSaveEdit = () => {
    if (!editingId) return;
    systemPrompts.update(editingId, {
      name: editName.trim(),
      content: editContent.trim(),
    });
    setEditingId(null);
    reload();
  };

  const handleDelete = (id: string) => {
    systemPrompts.delete(id);
    if (settings.voiceAssistantSystemPromptId === id) {
      updateSetting("voiceAssistantSystemPromptId", "builtin-voice-assistant");
    }
    reload();
  };

  const silenceMs = settings.voiceSilenceTimeoutMs ?? 1400;

  return (
    <>
      {/* ── Silence Timeout ── */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Mic className="h-4 w-4 text-primary" />
            Auto-Submit Silence Timeout
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-xs text-muted-foreground">
            In auto mode, the voice assistant waits this long after your last
            spoken word before automatically submitting the transcript. Increase
            it if you speak slowly or pause mid-sentence.
          </p>
          <div className="flex items-center gap-4">
            <input
              type="range"
              min={400}
              max={30000}
              step={100}
              value={silenceMs}
              onChange={(e) =>
                updateSetting("voiceSilenceTimeoutMs", Number(e.target.value))
              }
              className="flex-1 h-2 rounded-lg accent-primary"
            />
            <span className="w-20 text-right text-sm font-mono tabular-nums text-foreground">
              {silenceMs >= 1000
                ? `${(silenceMs / 1000).toFixed(1)} s`
                : `${silenceMs} ms`}
            </span>
          </div>
          <div className="flex justify-between text-[11px] text-muted-foreground">
            <span>0.4 s (fast)</span>
            <span>1.4 s (default)</span>
            <span>30 s (very slow)</span>
          </div>

          <Separator />

          <div className="flex items-center justify-between">
            <div>
              <Label>Restore system prompt on exit</Label>
              <p className="text-xs text-muted-foreground mt-0.5">
                When you leave voice mode, restore the previous system prompt
              </p>
            </div>
            <Switch
              checked={settings.voiceRestorePromptOnExit ?? true}
              onCheckedChange={(v) =>
                updateSetting("voiceRestorePromptOnExit", v)
              }
            />
          </div>
        </CardContent>
      </Card>

      {/* ── System Prompt Selection ── */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Speaker className="h-4 w-4 text-primary" />
            Voice Assistant System Prompt
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-xs text-muted-foreground">
            This prompt is automatically applied whenever voice mode activates
            (wake word or Speech to Text tab). It tells the model to keep
            responses short and conversational for audio playback.
          </p>

          <div className="space-y-1">
            <Label className="text-xs">Active prompt</Label>
            <Select
              value={selectedPromptId}
              onValueChange={(v) =>
                updateSetting("voiceAssistantSystemPromptId", v)
              }
            >
              <SelectTrigger className="text-sm">
                <SelectValue placeholder="Select a prompt…" />
              </SelectTrigger>
              <SelectContent>
                {builtinPrompts().map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.name}{" "}
                    <span className="text-muted-foreground text-xs ml-1">
                      (built-in)
                    </span>
                  </SelectItem>
                ))}
                {userPrompts.length > 0 && (
                  <>
                    <Separator className="my-1" />
                    {userPrompts.map((p) => (
                      <SelectItem key={p.id} value={p.id}>
                        {p.name}
                      </SelectItem>
                    ))}
                  </>
                )}
              </SelectContent>
            </Select>
          </div>

          {selectedPrompt && (
            <div className="rounded-md bg-muted/40 border px-3 py-2">
              <p className="text-[11px] font-semibold text-muted-foreground mb-1">
                Preview
              </p>
              <p className="text-xs text-foreground whitespace-pre-wrap leading-relaxed max-h-32 overflow-y-auto">
                {selectedPrompt.content}
              </p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* ── User Prompt Library ── */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Layers className="h-4 w-4 text-primary" />
            Custom Voice Prompts
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-xs text-muted-foreground">
            Create custom system prompts for different voice assistant
            personalities or use cases.
          </p>

          {/* Existing user prompts */}
          {userPrompts.length > 0 && (
            <div className="space-y-2">
              {userPrompts.map((p) => (
                <div
                  key={p.id}
                  className="rounded-lg border bg-muted/20 p-3 space-y-2"
                >
                  {editingId === p.id ? (
                    <>
                      <Input
                        value={editName}
                        onChange={(e) => setEditName(e.target.value)}
                        placeholder="Prompt name"
                        className="h-7 text-sm"
                      />
                      <textarea
                        value={editContent}
                        onChange={(e) => setEditContent(e.target.value)}
                        placeholder="System prompt content…"
                        className="w-full rounded-md border bg-background px-3 py-2 text-xs resize-none h-24 focus:outline-none focus:ring-2 focus:ring-primary/40"
                      />
                      <div className="flex gap-2">
                        <Button
                          size="sm"
                          onClick={handleSaveEdit}
                          className="h-7 text-xs"
                        >
                          <Check className="h-3 w-3 mr-1" /> Save
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setEditingId(null)}
                          className="h-7 text-xs"
                        >
                          Cancel
                        </Button>
                      </div>
                    </>
                  ) : (
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium truncate">{p.name}</p>
                        <p className="text-xs text-muted-foreground line-clamp-2 mt-0.5">
                          {p.content}
                        </p>
                      </div>
                      <div className="flex items-center gap-1 shrink-0">
                        <button
                          onClick={() => {
                            setEditingId(p.id);
                            setEditName(p.name);
                            setEditContent(p.content);
                          }}
                          className="h-6 w-6 flex items-center justify-center rounded text-muted-foreground hover:text-foreground transition-colors"
                          title="Edit"
                        >
                          <Pencil className="h-3 w-3" />
                        </button>
                        <button
                          onClick={() => handleDelete(p.id)}
                          className="h-6 w-6 flex items-center justify-center rounded text-muted-foreground hover:text-destructive transition-colors"
                          title="Delete"
                        >
                          <Trash2 className="h-3 w-3" />
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          <Separator />

          {/* Create new prompt */}
          <div className="space-y-2">
            <Label className="text-xs font-semibold">New custom prompt</Label>
            <Input
              value={newPromptName}
              onChange={(e) => setNewPromptName(e.target.value)}
              placeholder="Prompt name (e.g. 'Concise Assistant')"
              className="h-8 text-sm"
            />
            <textarea
              value={newPromptContent}
              onChange={(e) => setNewPromptContent(e.target.value)}
              placeholder="Write the system prompt here…"
              className="w-full rounded-md border bg-background px-3 py-2 text-xs resize-none h-28 focus:outline-none focus:ring-2 focus:ring-primary/40"
            />
            <Button
              size="sm"
              onClick={handleCreate}
              disabled={!newPromptName.trim() || !newPromptContent.trim()}
              className="h-8"
            >
              <Plus className="h-3.5 w-3.5 mr-1" /> Create Prompt
            </Button>
          </div>
        </CardContent>
      </Card>
    </>
  );
}
