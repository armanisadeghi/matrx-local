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
import { Badge, Button, Progress, ScrollArea, Separator } from "@ai-matrx/design-system";
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
  AudioLines,
  CheckCircle2,
  XCircle,
  HelpCircle,
  Loader2,
  ExternalLink,
  ShieldCheck,
  ChevronRight,
  AlertTriangle,
  Clock,
} from "lucide-react";
import { isTauri } from "@/lib/sidecar";
import { PLATFORM } from "@/lib/platformCtx";
import {
  isGranted,
  type PermissionKey,
  type PermissionState,
  type PermissionStatus,
} from "@/hooks/use-permissions";
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
  speech_recognition: <AudioLines className="h-5 w-5" />,
};

// Display order: most critical permissions first
const PERMISSION_ORDER: PermissionKey[] = [
  "accessibility",
  "screen_recording",
  "full_disk_access",
  "microphone",
  "input_monitoring",
  "camera",
  "bluetooth",
  "contacts",
  "calendar",
  "reminders",
  "photos",
  "location",
  "speech_recognition",
  "automation",
  "local_network",
];

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
    case "limited":
      return (
        <Badge className="gap-1 bg-green-500/15 text-green-600 dark:text-green-400 border-green-500/30 hover:bg-green-500/20">
          <CheckCircle2 className="h-3 w-3" />
          Limited
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
          Not asked yet
        </Badge>
      );
    case "first_use":
      return (
        <Badge variant="outline" className="gap-1 text-muted-foreground">
          <Clock className="h-3 w-3" />
          Asked on first use
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
          <HelpCircle className="h-3 w-3" />
          Could not read
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
  focused?: boolean;
}

function PermissionRow({
  state,
  isRequesting,
  onRequest,
  focused = false,
}: PermissionRowProps) {
  const granted = isGranted(state.status);
  const isUnavailable = state.status === "unavailable";
  const isRestricted = state.status === "restricted";
  // What one click does for this row — decided by the key's authority, so
  // the button never promises a prompt the OS will not show, and never sends
  // the person to a Settings pane where the app is not listed yet.
  const action: "grant" | "settings" | "none" =
    granted || isUnavailable || isRestricted || state.status === "loading"
      ? "none"
      : state.status === "not_determined" && state.canPrompt
        ? "grant"
        : "settings";

  return (
    <div
      id={`permission-row-${state.key}`}
      tabIndex={-1}
      className={`flex items-start gap-4 rounded-lg p-4 transition-colors ${
        granted
          ? "bg-green-500/5 dark:bg-green-500/5"
          : "bg-muted/40 hover:bg-muted/60"
      } ${focused ? "ring-2 ring-primary" : ""}`}
    >
      {/* Icon */}
      <div
        className={`mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${
          granted
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
        {granted ? (
          state.status === "limited" ? (
            <Button
              size="sm"
              variant="ghost"
              disabled={isRequesting}
              onClick={() => onRequest(state.key)}
              className="h-8 gap-1.5 text-xs"
              title="Granted with a scope you chose — change it in System Settings"
            >
              <ExternalLink className="h-3 w-3" />
              Adjust
            </Button>
          ) : (
            <CheckCircle2 className="mt-1 h-5 w-5 text-green-500" />
          )
        ) : isUnavailable ? (
          <span className="text-xs text-muted-foreground">N/A</span>
        ) : isRestricted ? (
          <span className="text-xs text-orange-500">MDM/Restricted</span>
        ) : action === "grant" ? (
          // A real prompt: the OS dialog comes from the process that owns
          // this key (this app for mic/camera/contacts/calendar/…; the
          // engine for screen recording), and the answer is read back.
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
                Waiting for macOS…
              </>
            ) : (
              <>
                Grant Access
                <ChevronRight className="h-3 w-3" />
              </>
            )}
          </Button>
        ) : action === "none" ? null : (
          // Denied, first-use, unknown, or Settings-only keys: the pane is
          // the only control there is.
          <Button
            size="sm"
            variant={state.status === "first_use" ? "outline" : "default"}
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
  focusKey,
}: PermissionsModalProps) {
  const { permissions, isLoading, summary, checkAll, request } = usePermissionsContext();
  const [requestingKey, setRequestingKey] = useState<PermissionKey | null>(null);

  // Re-check all when the modal opens
  useEffect(() => {
    if (open) {
      checkAll();
    }
  }, [open, checkAll]);

  useEffect(() => {
    if (!open || !focusKey) return;
    const timer = window.setTimeout(() => {
      const row = document.getElementById(`permission-row-${focusKey}`);
      row?.scrollIntoView({ behavior: "smooth", block: "center" });
      row?.focus({ preventScroll: true });
    }, 50);
    return () => window.clearTimeout(timer);
  }, [open, focusKey, permissions]);

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

  // Rows in display order. Counts come from the ONE shared summary so this
  // modal, the Dashboard and the Setup wizard can never disagree.
  const orderedStates = PERMISSION_ORDER.map((key) => permissions.get(key)).filter(
    Boolean,
  ) as PermissionState[];
  const grantedCount = summary.granted;
  const relevantCount = summary.total;
  const progressPercent = relevantCount > 0 ? Math.round((grantedCount / relevantCount) * 100) : 0;
  const ungrantedCount = Math.max(relevantCount - grantedCount, 0);
  const showCounts = summary.complete;

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
                {!showCounts ? (
                  <span className="flex items-center gap-1.5">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    Checking permissions…
                  </span>
                ) : (
                  `${grantedCount} of ${relevantCount} permissions granted`
                )}
              </span>
              {showCounts && ungrantedCount > 0 && (
                <span className="text-xs text-amber-600 dark:text-amber-400">
                  {ungrantedCount} need{ungrantedCount === 1 ? "s" : ""} attention
                </span>
              )}
              {showCounts && ungrantedCount === 0 && (
                <span className="text-xs text-green-600 dark:text-green-400 font-medium">
                  All permissions granted
                </span>
              )}
            </div>
            <Progress value={showCounts ? progressPercent : undefined} className="h-2" />
            {showCounts && summary.firstUse.length > 0 && (
              <p className="text-xs text-muted-foreground">
                {summary.firstUse.length === 1 ? "One permission is" : `${summary.firstUse.length} permissions are`}{" "}
                approved by macOS the first time the app uses them and {summary.firstUse.length === 1 ? "is" : "are"} not counted.
              </p>
            )}
          </div>
        </DialogHeader>

        <Separator />

        {/* Scrollable permission list */}
        <ScrollArea className="flex-1 overflow-auto">
          <div className="space-y-2 p-6 pt-4">
            {/* Ungranted first */}
            {orderedStates
              .filter((s) => !isGranted(s.status) && s.status !== "unavailable")
              .map((state) => (
                <PermissionRow
                  key={state.key}
                  state={state}
                  isRequesting={requestingKey === state.key}
                  onRequest={handleRequest}
                  focused={state.key === focusKey}
                />
              ))}

            {/* Granted (collapsed-looking section) */}
            {orderedStates.some((s) => isGranted(s.status)) && (
              <>
                <div className="py-2 text-xs font-medium uppercase tracking-wide text-muted-foreground/60">
                  Granted
                </div>
                {orderedStates
                  .filter((s) => isGranted(s.status))
                  .map((state) => (
                    <PermissionRow
                      key={state.key}
                      state={state}
                      isRequesting={false}
                      onRequest={handleRequest}
                      focused={state.key === focusKey}
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
