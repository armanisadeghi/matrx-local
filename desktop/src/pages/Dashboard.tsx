import { useState, useEffect, useCallback } from "react";
import { Link } from "react-router-dom";
import {
  Activity,
  Bluetooth,
  Chrome,
  Cpu,
  Download,
  Globe,
  Loader2,
  Mic,
  Monitor,
  Server,
  Shield,
  Wifi,
  Wrench,
  Zap,
  CheckCircle2,
  XCircle,
  HelpCircle,
  ArrowRight,
  User,
  Mail,
  LogOut,
} from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { SetupWizard } from "@/components/SetupWizard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { engine } from "@/lib/api";
import type { EngineStatus } from "@/hooks/use-engine";
import type { SystemInfo, BrowserStatus, PermissionInfo } from "@/lib/api";
import type { User as SupabaseUser } from "@supabase/supabase-js";
import { PermissionsModal } from "@/components/PermissionsModal";
import { usePermissionsContext } from "@/contexts/PermissionsContext";

interface DashboardProps {
  engineStatus: EngineStatus;
  engineUrl: string | null;
  tools: string[];
  systemInfo: SystemInfo | null;
  browserStatus: BrowserStatus | null;
  onRefresh: () => void;
  user: SupabaseUser | null;
  onSignOut?: () => void;
}

export function Dashboard({
  engineStatus,
  engineUrl,
  tools,
  systemInfo,
  browserStatus,
  onRefresh,
  user,
  onSignOut,
}: DashboardProps) {
  const [permissions, setPermissions] = useState<PermissionInfo[]>([]);
  const [installingBrowser, setInstallingBrowser] = useState(false);
  const [browserInstallMessage, setBrowserInstallMessage] = useState<
    string | null
  >(null);
  const [browserInstallError, setBrowserInstallError] = useState(false);
  const [permissionsModalOpen, setPermissionsModalOpen] = useState(false);

  // Tauri-plugin-backed permission states (authoritative TCC identity for the .app bundle)
  const { permissions: nativePermissions, isLoading: nativePermsLoading } =
    usePermissionsContext();

  const loadPermissions = useCallback(async () => {
    if (engineStatus !== "connected") return;
    try {
      const result = await engine.getDevicePermissions();
      setPermissions(result.permissions);
    } catch {
      // non-critical
    }
  }, [engineStatus]);

  useEffect(() => {
    loadPermissions();
  }, [loadPermissions]);

  const installBrowser = useCallback(async () => {
    setInstallingBrowser(true);
    setBrowserInstallMessage(null);
    setBrowserInstallError(false);
    try {
      const result = await engine.installCapability("browser_automation");
      const ok = result.status === "complete";
      if (ok) {
        setBrowserInstallMessage(
          "Installed successfully — restart the engine to activate.",
        );
        onRefresh();
      } else {
        setBrowserInstallError(true);
        setBrowserInstallMessage(
          `Install failed: ${result.error || result.message}`,
        );
      }
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      const engineCrashed =
        errMsg.includes("fetch") ||
        errMsg.includes("network") ||
        errMsg.includes("Failed to fetch") ||
        errMsg.includes("Load failed");
      setBrowserInstallError(true);
      setBrowserInstallMessage(
        engineCrashed
          ? "Engine became unreachable during install — the OS may have killed it. Restart the engine and try again."
          : `Error: ${errMsg}`,
      );
    } finally {
      setInstallingBrowser(false);
    }
  }, [onRefresh]);

  // Use native permissions from the Tauri plugin as source of truth for counts
  const nativeGrantedCount = Array.from(nativePermissions.values()).filter(
    (p) => p.status === "granted",
  ).length;
  const nativeTotalCount = Array.from(nativePermissions.values()).filter(
    (p) => p.status !== "unavailable" && p.status !== "loading",
  ).length;
  // Keep engine-checked counts as fallback when native check hasn't run yet
  const grantedCount =
    nativeTotalCount > 0
      ? nativeGrantedCount
      : permissions.filter((p) => p.status === "granted").length;
  const totalCount =
    nativeTotalCount > 0 ? nativeTotalCount : permissions.length;

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
            <BrowserStatusCard
              browserStatus={browserStatus}
              engineStatus={engineStatus}
              installing={installingBrowser}
              installMessage={browserInstallMessage}
              installError={browserInstallError}
              onInstall={installBrowser}
            />
            <StatusCard
              title="Device Access"
              value={totalCount > 0 ? `${grantedCount}/${totalCount}` : "---"}
              description={
                totalCount > 0
                  ? `${grantedCount} permissions granted`
                  : "Checking..."
              }
              icon={<Shield className="h-4 w-4" />}
              variant={
                grantedCount === totalCount && totalCount > 0
                  ? "success"
                  : grantedCount > 0
                    ? "warning"
                    : "default"
              }
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
                {nativePermsLoading && nativeTotalCount === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    Checking permissions…
                  </p>
                ) : nativeTotalCount > 0 ? (
                  <>
                    {Array.from(nativePermissions.values())
                      .filter((p) => p.status !== "unavailable")
                      .slice(0, 8)
                      .map((p) => (
                        <div
                          key={p.key}
                          className="flex items-center gap-3 rounded-md px-2 py-1.5 hover:bg-muted/50 transition-colors cursor-pointer"
                          onClick={() => setPermissionsModalOpen(true)}
                        >
                          <span className="text-muted-foreground">
                            {p.status === "granted" ? (
                              <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
                            ) : p.status === "denied" ? (
                              <XCircle className="h-3.5 w-3.5 text-red-500" />
                            ) : p.status === "loading" ? (
                              <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                            ) : (
                              <HelpCircle className="h-3.5 w-3.5 text-muted-foreground" />
                            )}
                          </span>
                          <span className="flex-1 text-sm">{p.label}</span>
                          {p.status !== "granted" && p.status !== "loading" && (
                            <span className="text-xs text-amber-500">
                              {p.status === "denied" ? "Denied" : "Not Granted"}
                            </span>
                          )}
                        </div>
                      ))}
                    {nativeTotalCount > 8 && (
                      <button
                        className="w-full text-xs text-muted-foreground hover:text-foreground pt-1 text-center"
                        onClick={() => setPermissionsModalOpen(true)}
                      >
                        View all {nativeTotalCount} permissions →
                      </button>
                    )}
                  </>
                ) : permissions.length > 0 ? (
                  <>
                    {permissions.map((p) => (
                      <DeviceStatusRow key={p.permission} perm={p} />
                    ))}
                  </>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    {engineStatus === "connected"
                      ? "Checking device permissions..."
                      : "Connect to engine to check device access"}
                  </p>
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

// Map permission keys to icons
const PERMISSION_ICONS: Record<string, React.ReactNode> = {
  microphone: <Mic className="h-3.5 w-3.5" />,
  camera: <Monitor className="h-3.5 w-3.5" />,
  accessibility: <Shield className="h-3.5 w-3.5" />,
  bluetooth: <Bluetooth className="h-3.5 w-3.5" />,
  network: <Wifi className="h-3.5 w-3.5" />,
  screen_recording: <Monitor className="h-3.5 w-3.5" />,
  location: <Globe className="h-3.5 w-3.5" />,
};

const PERMISSION_LABELS: Record<string, string> = {
  microphone: "Microphone",
  camera: "Camera",
  accessibility: "Accessibility",
  bluetooth: "Bluetooth",
  network: "Network",
  screen_recording: "Screen Recording",
  location: "Location",
};

function DeviceStatusRow({ perm }: { perm: PermissionInfo }) {
  const icon = PERMISSION_ICONS[perm.permission] ?? (
    <HelpCircle className="h-3.5 w-3.5" />
  );
  const label = PERMISSION_LABELS[perm.permission] ?? perm.permission;

  const statusIcon =
    perm.status === "granted" ? (
      <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
    ) : perm.status === "denied" ? (
      <XCircle className="h-3.5 w-3.5 text-red-500" />
    ) : (
      <HelpCircle className="h-3.5 w-3.5 text-muted-foreground" />
    );

  return (
    <div className="flex items-center gap-3 rounded-md px-2 py-1.5 hover:bg-muted/50 transition-colors">
      <span className="text-muted-foreground">{icon}</span>
      <span className="flex-1 text-sm">{label}</span>
      {statusIcon}
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

function BrowserStatusCard({
  browserStatus,
  engineStatus,
  installing,
  installMessage,
  installError,
  onInstall,
}: {
  browserStatus: BrowserStatus | null;
  engineStatus: EngineStatus;
  installing: boolean;
  installMessage: string | null;
  installError: boolean;
  onInstall: () => void;
}) {
  const isReady = browserStatus?.chrome_found === true;
  const isChecking = browserStatus === null;
  const canInstall = engineStatus === "connected" && !isReady && !isChecking;

  const variant = isChecking ? "default" : isReady ? "success" : "warning";
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
          <span className="text-sm text-muted-foreground">
            Browser (Playwright)
          </span>
          <span className={indicatorColor}>
            <Chrome className="h-4 w-4" />
          </span>
        </div>
        <div className="mt-2">
          <span className="text-2xl font-bold">
            {isChecking ? "Checking..." : isReady ? "Ready" : "Not Installed"}
          </span>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          {isReady
            ? browserStatus?.chrome_version
              ? `Chromium ${browserStatus.chrome_version}`
              : "Playwright ready"
            : isChecking
              ? "Detecting browser..."
              : "Required for browser-based scraping"}
        </p>
        {canInstall && (
          <Button
            size="sm"
            variant="outline"
            className="mt-2 h-7 w-full gap-1.5 text-xs"
            onClick={onInstall}
            disabled={installing}
          >
            {installing ? (
              <>
                <Loader2 className="h-3 w-3 animate-spin" />
                Installing... (~60s)
              </>
            ) : (
              <>
                <Download className="h-3 w-3" />
                Install Chromium
              </>
            )}
          </Button>
        )}
        {installMessage && (
          <p
            className={`mt-1.5 text-[11px] leading-tight ${installError ? "text-red-400" : "text-emerald-400"}`}
          >
            {installMessage}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
