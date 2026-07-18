/**
 * PermissionsModal — Step-by-step macOS permissions setup experience.
 *
 * Displays all required permissions with current status, allows the user to
 * grant each one individually, and shows live status after they return from
 * System Settings. Can be opened from:
 *   - SetupWizard permissions step
 *   - Dashboard "Review & Grant" button
 *   - Inline permission-denied toast (via onRequestKey prop)
 */

import { useCallback, useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Progress } from "@/components/ui/progress";
import { Separator } from "@/components/ui/separator";
import {
  Mic,
  Camera,
  Monitor,
  Accessibility,
  HardDrive,
  Keyboard,
  BookUser,
  CalendarDays,
  Image,
  Bell,
  Bluetooth,
  MapPin,
  Network,
  Terminal,
  Wifi,
  MessageSquare,
  Mail,
  AudioLines,
  CheckCircle2,
  XCircle,
  HelpCircle,
  Loader2,
  ExternalLink,
  ShieldCheck,
  ChevronRight,
  AlertTriangle,
} from "lucide-react";
import { isTauri } from "@/lib/sidecar";
import { PLATFORM } from "@/lib/platformCtx";
import type { PermissionKey, PermissionState, PermissionStatus } from "@/hooks/use-permissions";
import { usePermissionsContext } from "@/contexts/PermissionsContext";

// ---------------------------------------------------------------------------
// Icons per permission
// ---------------------------------------------------------------------------

const PERMISSION_ICONS: Record<PermissionKey, React.ReactNode> = {
  microphone: <Mic className="h-5 w-5" />,
  camera: <Camera className="h-5 w-5" />,
  screen_recording: <Monitor className="h-5 w-5" />,
  accessibility: <Accessibility className="h-5 w-5" />,
  full_disk_access: <HardDrive className="h-5 w-5" />,
  input_monitoring: <Keyboard className="h-5 w-5" />,
  contacts: <BookUser className="h-5 w-5" />,
  calendar: <CalendarDays className="h-5 w-5" />,
  reminders: <Bell className="h-5 w-5" />,
  photos: <Image className="h-5 w-5" />,
  bluetooth: <Bluetooth className="h-5 w-5" />,
  location: <MapPin className="h-5 w-5" />,
  local_network: <Network className="h-5 w-5" />,
  automation: <Terminal className="h-5 w-5" />,
  network: <Wifi className="h-5 w-5" />,
  messages: <MessageSquare className="h-5 w-5" />,
  mail: <Mail className="h-5 w-5" />,
  speech_recognition: <AudioLines className="h-5 w-5" />,
};

// Display order: most critical permissions first
const PERMISSION_ORDER: PermissionKey[] = [
  "accessibility",
  "screen_recording",
  "full_disk_access",
  "microphone",
  "input_monitoring",
  "automation",
  "camera",
  "bluetooth",
  "local_network",
  "messages",
  "mail",
  "contacts",
  "calendar",
  "reminders",
  "photos",
  "location",
  "speech_recognition",
  "network",
];

// macOS permissions that must be triggered by app usage before they appear in Settings
const TRIGGER_REQUIRED_KEYS = new Set<PermissionKey>([
  "screen_recording",
  "automation",
  "local_network",
  "mail",
  "contacts",
  "calendar",
  "reminders",
  "photos",
  "location",
  "speech_recognition",
]);

// ---------------------------------------------------------------------------
// Status badge helpers
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: PermissionStatus }) {
  switch (status) {
    case "granted":
      return (
        <Badge className="gap-1 bg-green-500/15 text-green-600 dark:text-green-400 border-green-500/30 hover:bg-green-500/20">
          <CheckCircle2 className="h-3 w-3" />
          Granted
        </Badge>
      );
    case "denied":
      return (
        <Badge className="gap-1 bg-red-500/15 text-red-600 dark:text-red-400 border-red-500/30 hover:bg-red-500/20">
          <XCircle className="h-3 w-3" />
          Denied
        </Badge>
      );
    case "not_determined":
      return (
        <Badge variant="outline" className="gap-1 text-muted-foreground">
          <HelpCircle className="h-3 w-3" />
          Not Requested
        </Badge>
      );
    case "restricted":
      return (
        <Badge className="gap-1 bg-orange-500/15 text-orange-600 dark:text-orange-400 border-orange-500/30">
          <AlertTriangle className="h-3 w-3" />
          Restricted
        </Badge>
      );
    case "unavailable":
      return (
        <Badge variant="outline" className="gap-1 text-muted-foreground opacity-60">
          Unavailable
        </Badge>
      );
    case "loading":
      return (
        <Badge variant="outline" className="gap-1 text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" />
          Checking...
        </Badge>
      );
    default:
      return (
        <Badge variant="outline" className="gap-1 text-muted-foreground">
          Unknown
        </Badge>
      );
  }
}

// ---------------------------------------------------------------------------
// Individual permission row
// ---------------------------------------------------------------------------

interface PermissionRowProps {
  state: PermissionState;
  isRequesting: boolean;
  onRequest: (key: PermissionKey) => void;
}

function PermissionRow({
  state,
  isRequesting,
  onRequest,
}: PermissionRowProps) {
  const isGranted = state.status === "granted";
  const isUnavailable = state.status === "unavailable";
  const isRestricted = state.status === "restricted";

  return (
    <div
      className={`flex items-start gap-4 rounded-lg p-4 transition-colors ${
        isGranted
          ? "bg-green-500/5 dark:bg-green-500/5"
          : "bg-muted/40 hover:bg-muted/60"
      }`}
    >
      {/* Icon */}
      <div
        className={`mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${
          isGranted
            ? "bg-green-500/15 text-green-600 dark:text-green-400"
            : "bg-muted text-muted-foreground"
        }`}
      >
        {PERMISSION_ICONS[state.key]}
      </div>

      {/* Content */}
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium leading-none">{state.label}</span>
          <StatusBadge status={state.status} />
        </div>
        <p className="text-xs text-muted-foreground">{state.description}</p>
        {state.tools.length > 0 && (
          <p className="text-xs text-muted-foreground/70">
            Used by: {state.tools.slice(0, 4).join(", ")}
            {state.tools.length > 4 && ` +${state.tools.length - 4} more`}
          </p>
        )}
        {state.detail && (
          <p className="text-xs text-muted-foreground/70 italic">{state.detail}</p>
        )}
      </div>

      {/* Actions */}
      <div className="flex shrink-0 flex-col items-end gap-2">
        {isGranted ? (
          <CheckCircle2 className="mt-1 h-5 w-5 text-green-500" />
        ) : isUnavailable ? (
          <span className="text-xs text-muted-foreground">N/A</span>
        ) : isRestricted ? (
          <span className="text-xs text-orange-500">MDM/Restricted</span>
        ) : (state.key === "microphone" ||
            state.key === "camera" ||
            state.key === "screen_recording") &&
          state.status === "not_determined" ? (
          // Mic & camera: AVFoundation can show an in-app OS dialog the first time.
          // Screen recording: the engine's CGRequestScreenCaptureAccess shows the
          // native prompt AND is what finally lists the engine in System Settings —
          // so this is a real "Grant", not an "Open Settings" dead end.
          <Button
            size="sm"
            variant="default"
            disabled={isRequesting}
            onClick={() => onRequest(state.key)}
            className="h-8 gap-1.5 text-xs"
          >
            {isRequesting ? (
              <>
                <Loader2 className="h-3 w-3 animate-spin" />
                Requesting…
              </>
            ) : (
              <>
                Grant Access
                <ChevronRight className="h-3 w-3" />
              </>
            )}
          </Button>
        ) : PLATFORM.is_mac && state.status === "not_determined" && TRIGGER_REQUIRED_KEYS.has(state.key) ? (
          // MacOS requires these to be triggered by app usage first
          <Button
            size="sm"
            variant="outline"
            disabled
            className="h-8 gap-1.5 text-xs text-amber-600 dark:text-amber-400 border-amber-500/30 bg-amber-500/10 opacity-100"
          >
            <AlertTriangle className="h-3 w-3" />
            Open Feature to Approve
          </Button>
        ) : (
          // All others: open the specific System Settings pane
          <Button
            size="sm"
            variant="default"
            disabled={isRequesting}
            onClick={() => onRequest(state.key)}
            className="h-8 gap-1.5 text-xs"
          >
            {isRequesting ? (
              <>
                <Loader2 className="h-3 w-3 animate-spin" />
                Opening…
              </>
            ) : (
              <>
                <ExternalLink className="h-3 w-3" />
                Open Settings
              </>
            )}
          </Button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Modal
// ---------------------------------------------------------------------------

interface PermissionsModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** If provided, the modal scrolls to and highlights this permission on open */
  focusKey?: PermissionKey | null;
}

export function PermissionsModal({
  open,
  onOpenChange,
  focusKey: _focusKey,
}: PermissionsModalProps) {
  const { permissions, isLoading, checkAll, request } = usePermissionsContext();
  const [requestingKey, setRequestingKey] = useState<PermissionKey | null>(null);

  // Re-check all when the modal opens
  useEffect(() => {
    if (open) {
      checkAll();
    }
  }, [open, checkAll]);

  const handleRequest = useCallback(
    async (key: PermissionKey) => {
      setRequestingKey(key);
      try {
        await request(key);
      } finally {
        setRequestingKey(null);
      }
    },
    [request],
  );

  // Compute stats
  const orderedStates = PERMISSION_ORDER.map((key) => permissions.get(key)).filter(
    Boolean,
  ) as PermissionState[];

  const grantedCount = orderedStates.filter((s) => s.status === "granted").length;
  const relevantCount = orderedStates.filter((s) => s.status !== "unavailable").length;
  const progressPercent = relevantCount > 0 ? Math.round((grantedCount / relevantCount) * 100) : 0;

  const ungrantedCount = orderedStates.filter(
    (s) => s.status !== "granted" && s.status !== "unavailable" && s.status !== "loading",
  ).length;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[90vh] w-full max-w-2xl flex-col gap-0 p-0">
        {/* Header */}
        <DialogHeader className="space-y-3 p-6 pb-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10">
              <ShieldCheck className="h-5 w-5 text-primary" />
            </div>
            <div>
              <DialogTitle className="text-xl">System Permissions</DialogTitle>
              <DialogDescription className="text-sm">
                AI Matrx needs these permissions to run automation and AI tools on {PLATFORM.is_mac ? "your Mac" : PLATFORM.is_windows ? "your PC" : "your system"}.
              </DialogDescription>
            </div>
          </div>

          {/* Progress bar */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">
                {isLoading ? (
                  <span className="flex items-center gap-1.5">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    Checking permissions…
                  </span>
                ) : (
                  `${grantedCount} of ${relevantCount} permissions granted`
                )}
              </span>
              {!isLoading && ungrantedCount > 0 && (
                <span className="text-xs text-amber-600 dark:text-amber-400">
                  {ungrantedCount} need{ungrantedCount === 1 ? "s" : ""} attention
                </span>
              )}
              {!isLoading && ungrantedCount === 0 && (
                <span className="text-xs text-green-600 dark:text-green-400 font-medium">
                  All permissions granted
                </span>
              )}
            </div>
            <Progress value={isLoading ? undefined : progressPercent} className="h-2" />
          </div>
        </DialogHeader>

        <Separator />

        {/* Scrollable permission list */}
        <ScrollArea className="flex-1 overflow-auto">
          <div className="space-y-2 p-6 pt-4">
            {/* Ungranted first */}
            {orderedStates
              .filter((s) => s.status !== "granted" && s.status !== "unavailable")
              .map((state) => (
                <PermissionRow
                  key={state.key}
                  state={state}
                  isRequesting={requestingKey === state.key}
                  onRequest={handleRequest}
                />
              ))}

            {/* Granted (collapsed-looking section) */}
            {orderedStates.some((s) => s.status === "granted") && (
              <>
                <div className="py-2 text-xs font-medium uppercase tracking-wide text-muted-foreground/60">
                  Granted
                </div>
                {orderedStates
                  .filter((s) => s.status === "granted")
                  .map((state) => (
                    <PermissionRow
                      key={state.key}
                      state={state}
                      isRequesting={false}
                      onRequest={handleRequest}
                    />
                  ))}
              </>
            )}
          </div>
        </ScrollArea>

        <Separator />

        {/* Footer */}
        <div className="flex items-center justify-between p-4 pt-3">
          <p className="text-xs text-muted-foreground max-w-sm">
            Permissions are stored by macOS and can be revoked any time in{" "}
            <button
              className="underline underline-offset-2 hover:text-foreground"
              onClick={() => {
                const url = "x-apple.systempreferences:com.apple.preference.security?Privacy";
                if (isTauri()) {
                  import("@tauri-apps/plugin-shell").then(({ open }) => open(url)).catch(() => {});
                } else {
                  window.open(url, "_blank");
                }
              }}
            >
              System Settings → Privacy & Security
            </button>
            .
          </p>
          <div className="flex gap-2 shrink-0">
            <Button variant="outline" size="sm" onClick={() => checkAll()} disabled={isLoading}>
              {isLoading ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                "Refresh"
              )}
            </Button>
            <Button size="sm" onClick={() => onOpenChange(false)}>
              Done
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
