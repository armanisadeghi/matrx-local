import { useState, useEffect, useCallback } from "react";
import { Link } from "react-router-dom";
import {
  Activity,
  Chrome,
  Cpu,
  Download,
  Loader2,
  Server,
  Shield,
  Wrench,
  Zap,
  CheckCircle2,
  XCircle,
  HelpCircle,
  ArrowRight,
  User,
  Mail,
  LogOut,
  RefreshCw,
  Clock,
} from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { SetupWizard } from "@/components/SetupWizard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge, Button } from "@ai-matrx/design-system";
import { engine } from "@/lib/api";
import type { EngineStatus } from "@/hooks/use-engine";
import type { SystemInfo } from "@/lib/api";
import type { User as SupabaseUser } from "@supabase/supabase-js";
import { PermissionsModal } from "@/components/PermissionsModal";
import { usePermissionsContext } from "@/contexts/PermissionsContext";
import { useBrowserRuntimeContext } from "@/contexts/BrowserRuntimeContext";
import { isGranted, type PermissionState } from "@/hooks/use-permissions";

interface DashboardProps {
  engineStatus: EngineStatus;
  engineUrl: string | null;
  tools: string[];
  systemInfo: SystemInfo | null;
  onRefresh: () => void;
  user: SupabaseUser | null;
  onSignOut?: () => void;
}

export function Dashboard({
  engineStatus,
  engineUrl,
  tools,
  systemInfo,
  onRefresh,
  user,
  onSignOut,
}: DashboardProps) {
  const [permissionsModalOpen, setPermissionsModalOpen] = useState(false);

  // ONE permission model app-wide (PermissionsContext). The count comes from
  // `summary`, computed over queryable keys only and reported only once every
  // key has answered — the Dashboard never shows a number that changes its
  // denominator a few seconds later.
  const { permissions, summary, refreshDevicePermissions } =
    usePermissionsContext();

  useEffect(() => {
    if (engineStatus !== "connected") return;
    // Engine-owned keys (screen recording) answer only once the engine is up.
    void refreshDevicePermissions();
  }, [engineStatus, refreshDevicePermissions]);

  const deviceAccessValue = summary.complete
    ? `${summary.granted}/${summary.total}`
    : "—";
  const deviceAccessDescription = summary.complete
    ? summary.unknown.length > 0
      ? `${summary.granted} granted · ${summary.unknown.length} could not be read`
      : `${summary.granted} of ${summary.total} permissions granted`
    : "Checking permissions…";
  const deviceAccessVariant: "success" | "warning" | "default" = !summary.complete
    ? "default"
    : summary.total > 0 && summary.granted === summary.total
      ? "success"
      : "warning";

  const visibleRows = Array.from(permissions.values()).filter(
    (p) => p.status !== "unavailable",
  );

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PageHeader
        title="Dashboard"
        description="System overview and engine status"
      >
        <Button variant="ghost" size="sm" onClick={onRefresh}>
          <Activity className="h-4 w-4" />
          Refresh
        </Button>
      </PageHeader>

      <div className="flex-1 overflow-auto p-6">
        <div className="mx-auto max-w-6xl space-y-6">
          {/* User Profile Card */}
          <Card>
            <CardContent className="p-4">
              <div className="flex items-center gap-4">
                {/* Avatar */}
                <div className="relative shrink-0">
                  {user?.user_metadata?.avatar_url ? (
                    <img
                      src={user.user_metadata.avatar_url}
                      alt="Profile"
                      className="h-12 w-12 rounded-full object-cover"
                    />
                  ) : (
                    <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary/10 text-primary">
                      <User className="h-6 w-6" />
                    </div>
                  )}
                  <span className="absolute -bottom-0.5 -right-0.5 h-3.5 w-3.5 rounded-full border-2 border-background bg-emerald-500" />
                </div>

                {/* User info */}
                <div className="flex-1 min-w-0">
                  <p className="font-semibold truncate">
                    {user?.user_metadata?.full_name ??
                      user?.user_metadata?.name ??
                      user?.user_metadata?.user_name ??
                      "Signed In"}
                  </p>
                  <p className="flex items-center gap-1 text-xs text-muted-foreground truncate">
                    <Mail className="h-3 w-3 shrink-0" />
                    {user?.email ?? "—"}
                  </p>
                  <p className="mt-0.5 text-[10px] text-muted-foreground capitalize">
                    {user?.app_metadata?.provider ?? "email"}
                  </p>
                </div>

                {/* Sign out */}
                {onSignOut && (
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8 shrink-0 text-muted-foreground hover:text-foreground"
                    onClick={onSignOut}
                    title="Sign out"
                  >
                    <LogOut className="h-4 w-4" />
                  </Button>
                )}
              </div>
            </CardContent>
          </Card>

          {/* Setup Wizard */}
          <SetupWizard
            engineStatus={engineStatus}
            onSetupComplete={onRefresh}
          />

          {/* Status Cards Row */}
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            <StatusCard
              title="Engine"
              value={engineStatus === "connected" ? "Online" : "Offline"}
              description={engineUrl ?? "Not discovered"}
              icon={<Server className="h-4 w-4" />}
              variant={engineStatus === "connected" ? "success" : "warning"}
            />
            <StatusCard
              title="Tools Available"
              value={String(tools.length)}
              description="Registered tools"
              icon={<Wrench className="h-4 w-4" />}
              variant="default"
            />
            <BrowserStatusCard engineStatus={engineStatus} />
            <StatusCard
              title="Device Access"
              value={deviceAccessValue}
              description={deviceAccessDescription}
              icon={<Shield className="h-4 w-4" />}
              variant={deviceAccessVariant}
            />
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            {/* System Information */}
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Cpu className="h-4 w-4 text-primary" />
                  System Information
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {systemInfo ? (
                  <>
                    <InfoRow label="Hostname" value={systemInfo.hostname} />
                    <InfoRow label="Platform" value={systemInfo.platform} />
                    <InfoRow
                      label="Architecture"
                      value={systemInfo.architecture}
                    />
                    <InfoRow label="Python" value={systemInfo.python_version} />
                    <InfoRow label="User" value={systemInfo.username} />
                    <InfoRow
                      label="Working Directory"
                      value={systemInfo.cwd}
                      mono
                    />
                  </>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    {engineStatus === "connected"
                      ? "Loading system information..."
                      : "Connect to engine to view system information"}
                  </p>
                )}
              </CardContent>
            </Card>

            {/* Device Access Overview */}
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="flex items-center justify-between text-base">
                  <span className="flex items-center gap-2">
                    <Shield className="h-4 w-4 text-primary" />
                    Device Access
                  </span>
                  <div className="flex items-center gap-1">
                    <Button
                      variant="default"
                      size="sm"
                      className="h-7 text-xs gap-1"
                      onClick={() => setPermissionsModalOpen(true)}
                    >
                      Review & Grant
                    </Button>
                    <Link to="/devices">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 text-xs gap-1"
                      >
                        Manage
                        <ArrowRight className="h-3 w-3" />
                      </Button>
                    </Link>
                  </div>
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {visibleRows.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    No device permissions apply on this platform.
                  </p>
                ) : (
                  <>
                    {visibleRows.map((p) => (
                      <PermissionRow
                        key={p.key}
                        state={p}
                        onClick={() => setPermissionsModalOpen(true)}
                      />
                    ))}
                  </>
                )}
              </CardContent>
            </Card>

            <PermissionsModal
              open={permissionsModalOpen}
              onOpenChange={setPermissionsModalOpen}
            />

            {/* Tools Overview */}
            <Card className="lg:col-span-2">
              <CardHeader className="pb-3">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Zap className="h-4 w-4 text-primary" />
                  Available Tools ({tools.length})
                </CardTitle>
              </CardHeader>
              <CardContent>
                {tools.length > 0 ? (
                  <div className="flex flex-wrap gap-2">
                    {tools.map((tool) => (
                      <Badge key={tool} variant="secondary" className="text-xs">
                        {tool}
                      </Badge>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    No tools loaded. Connect to the engine to see available
                    tools.
                  </p>
                )}
              </CardContent>
            </Card>
          </div>
        </div>
      </div>
    </div>
  );
}

/** Plain-language status word for a permission row. Every status has one. */
export function permissionStatusLabel(status: PermissionState["status"]): string {
  switch (status) {
    case "granted":
      return "Granted";
    case "limited":
      return "Limited";
    case "denied":
      return "Denied";
    case "restricted":
      return "Restricted";
    case "not_determined":
      return "Not asked yet";
    case "first_use":
      return "Asked on first use";
    case "loading":
      return "Checking…";
    case "unavailable":
      return "Not on this platform";
    default:
      return "Could not read";
  }
}

function PermissionRow({
  state,
  onClick,
}: {
  state: PermissionState;
  onClick: () => void;
}) {
  const granted = isGranted(state.status);
  const icon =
    state.status === "loading" ? (
      <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
    ) : granted ? (
      <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
    ) : state.status === "denied" || state.status === "restricted" ? (
      <XCircle className="h-3.5 w-3.5 text-red-500" />
    ) : state.status === "first_use" ? (
      <Clock className="h-3.5 w-3.5 text-muted-foreground" />
    ) : (
      <HelpCircle className="h-3.5 w-3.5 text-muted-foreground" />
    );
  const trailing =
    state.status === "granted"
      ? null
      : state.status === "loading"
        ? null
        : state.status === "first_use"
          ? "text-muted-foreground"
          : state.status === "limited"
            ? "text-emerald-600"
            : "text-amber-500";

  return (
    <div
      className="flex items-center gap-3 rounded-md px-2 py-1.5 hover:bg-muted/50 transition-colors cursor-pointer"
      onClick={onClick}
    >
      <span className="text-muted-foreground">{icon}</span>
      <span className="flex-1 text-sm">{state.label}</span>
      {trailing && (
        <span className={`text-xs ${trailing}`}>
          {permissionStatusLabel(state.status)}
        </span>
      )}
    </div>
  );
}

function StatusCard({
  title,
  value,
  description,
  icon,
  variant,
}: {
  title: string;
  value: string;
  description: string;
  icon: React.ReactNode;
  variant: "success" | "warning" | "default";
}) {
  const indicatorColor =
    variant === "success"
      ? "text-emerald-500"
      : variant === "warning"
        ? "text-amber-500"
        : "text-muted-foreground";

  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex items-center justify-between">
          <span className="text-sm text-muted-foreground">{title}</span>
          <span className={indicatorColor}>{icon}</span>
        </div>
        <div className="mt-2">
          <span className="text-2xl font-bold">{value}</span>
        </div>
        <p className="mt-1 truncate text-xs text-muted-foreground">
          {description}
        </p>
      </CardContent>
    </Card>
  );
}

function InfoRow({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span
        className={`text-sm truncate max-w-[300px] ${mono ? "font-mono text-xs" : ""}`}
      >
        {value}
      </span>
    </div>
  );
}

/**
 * What the Browser card says for each engine-reported state. ONE source of
 * truth — `GET /browser-runtime/status` via BrowserRuntimeContext — the same
 * answer the top banner and the Scraping page use. (This card used to read a
 * different signal, a package-import flag captured at engine start, and said
 * "Not Installed" while the banner said "needs a restart" about the same
 * browser.)
 */
export function browserCardPresentation(
  status: { code: string; available: boolean; install_percent: number | null } | null,
  loaded: boolean,
  installing: boolean,
): {
  value: string;
  description: string;
  variant: "success" | "warning" | "default";
  action: "install" | "repair" | null;
} {
  if (!loaded || status === null) {
    return {
      value: "Checking…",
      description: "Detecting browser…",
      variant: "default",
      action: null,
    };
  }
  if (installing || status.code === "installing") {
    const pct = status.install_percent;
    return {
      value: "Installing…",
      description: pct !== null ? `${pct}% downloaded` : "Downloading Chromium",
      variant: "default",
      action: null,
    };
  }
  if (status.available) {
    return {
      value: "Ready",
      description: "Chromium is running for browser-based scraping",
      variant: "success",
      action: null,
    };
  }
  switch (status.code) {
    case "browser_starting":
      return {
        value: "Starting…",
        description: "Chromium is starting; no action needed",
        variant: "default",
        action: null,
      };
    case "browser_launch_failed":
      return {
        value: "Not running",
        description: "Chromium is installed but did not start",
        variant: "warning",
        action: "repair",
      };
    case "playwright_package_missing":
    case "browser_not_installed":
    default:
      return {
        value: "Not installed",
        description: "Needed for browser-based scraping (~90 MB)",
        variant: "warning",
        action: "install",
      };
  }
}

function BrowserStatusCard({ engineStatus }: { engineStatus: EngineStatus }) {
  const { status, loaded, installing, percent, message, error, actions } =
    useBrowserRuntimeContext();
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const view = browserCardPresentation(status, loaded, installing);
  const indicatorColor =
    view.variant === "success"
      ? "text-emerald-500"
      : view.variant === "warning"
        ? "text-amber-500"
        : "text-muted-foreground";

  const run = useCallback(async () => {
    setBusy(true);
    setLocalError(null);
    try {
      if (status?.code === "playwright_package_missing") {
        // The Playwright package itself is absent from this engine; the
        // browser download needs it first. One click does both.
        const result = await engine.installCapability("browser_automation");
        if (result.status !== "complete") {
          setLocalError(
            `Could not install the browser driver: ${result.error || result.message}`,
          );
          return;
        }
      }
      // Downloads Chromium when it is missing; when it is on disk and merely
      // not running, the engine skips the download and just starts it.
      await actions.install();
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [actions, status?.code]);

  const canAct =
    engineStatus === "connected" && view.action !== null && !busy && !installing;
  const shownError = localError ?? error;

  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex items-center justify-between">
          <span className="text-sm text-muted-foreground">
            Browser (Playwright)
          </span>
          <span className={indicatorColor}>
            <Chrome className="h-4 w-4" />
          </span>
        </div>
        <div className="mt-2">
          <span className="text-2xl font-bold">{view.value}</span>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          {installing && message ? `${message} (${percent}%)` : view.description}
        </p>
        {view.action && (
          <Button
            size="sm"
            variant="outline"
            className="mt-2 h-7 w-full gap-1.5 text-xs"
            onClick={() => void run()}
            disabled={!canAct}
          >
            {busy || installing ? (
              <>
                <Loader2 className="h-3 w-3 animate-spin" />
                {view.action === "repair" ? "Starting…" : "Installing…"}
              </>
            ) : view.action === "repair" ? (
              <>
                <RefreshCw className="h-3 w-3" />
                Start browser
              </>
            ) : (
              <>
                <Download className="h-3 w-3" />
                Install Chromium
              </>
            )}
          </Button>
        )}
        {shownError && (
          <p className="mt-1.5 text-[11px] leading-tight text-red-400">
            {shownError}
          </p>
        )}
        {!view.action && status?.reason && !status.available && (
          <p className="mt-1.5 text-[11px] leading-tight text-muted-foreground">
            {status.reason}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
