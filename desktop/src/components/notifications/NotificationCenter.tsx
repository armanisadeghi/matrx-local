import { useRef, useEffect, useState } from "react";
import { Bell, X, CheckCheck, Trash2, Info, CheckCircle2, AlertTriangle, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { AppNotification, NotificationLevel } from "@/hooks/use-notifications";
import { dispatchActionNeeded } from "@/features/action-needed";

// ── Per-level styling ─────────────────────────────────────────────────────

const LEVEL_CONFIG: Record<NotificationLevel, {
  icon: React.ElementType;
  iconClass: string;
  barClass: string;
  bgClass: string;
}> = {
  info:    { icon: Info,          iconClass: "text-sky-400",     barClass: "bg-sky-500",     bgClass: "bg-sky-500/8" },
  success: { icon: CheckCircle2,  iconClass: "text-emerald-400", barClass: "bg-emerald-500", bgClass: "bg-emerald-500/8" },
  warning: { icon: AlertTriangle, iconClass: "text-amber-400",   barClass: "bg-amber-500",   bgClass: "bg-amber-500/8" },
  error:   { icon: XCircle,       iconClass: "text-red-400",     barClass: "bg-red-500",     bgClass: "bg-red-500/8" },
};

function timeAgo(ts: number): string {
  const diff = Math.floor((Date.now() - ts) / 1000);
  if (diff < 5)  return "just now";
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

// ── Toast (auto-dismiss popup) ────────────────────────────────────────────

interface ToastProps {
  notification: AppNotification;
  onDismiss: (id: string) => void;
}

function NotificationToast({ notification: n, onDismiss }: ToastProps) {
  const cfg = LEVEL_CONFIG[n.level];
  const Icon = cfg.icon;

  useEffect(() => {
    const t = setTimeout(() => onDismiss(n.id), 6000);
    return () => clearTimeout(t);
  }, [n.id, onDismiss]);

  return (
    <div
      className={cn(
        "relative flex items-start gap-3 rounded-xl border border-border/60 p-3.5 shadow-lg backdrop-blur-sm",
        "animate-in slide-in-from-right-8 fade-in duration-300",
        cfg.bgClass,
      )}
      style={{ minWidth: 300, maxWidth: 380 }}
    >
      {/* left colour bar */}
      <div className={cn("absolute left-0 top-3 bottom-3 w-0.5 rounded-full", cfg.barClass)} />
      <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", cfg.iconClass)} />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold leading-tight">{n.title}</p>
        <p className="mt-0.5 text-xs text-muted-foreground leading-snug line-clamp-3">{n.message}</p>
        {n.actionNeeded && (
          <Button
            size="sm"
            className="mt-2 h-6 px-2 text-[11px]"
            onClick={() => void dispatchActionNeeded(n.actionNeeded!)}
          >
            {n.actionNeeded.action.label}
          </Button>
        )}
      </div>
      <button
        onClick={() => onDismiss(n.id)}
        className="shrink-0 text-muted-foreground hover:text-foreground transition-colors"
        aria-label={`Hide ${n.title} toast`}
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

// ── Toast container (fixed bottom-right) ─────────────────────────────────

interface ToastContainerProps {
  toasts: AppNotification[];
  onDismiss: (id: string) => void;
}

export function NotificationToastContainer({ toasts, onDismiss }: ToastContainerProps) {
  if (toasts.length === 0) return null;
  return (
    <div className="fixed bottom-10 right-4 z-50 flex flex-col gap-2 pointer-events-none">
      {toasts.map((n) => (
        <div key={n.id} className="pointer-events-auto">
          <NotificationToast notification={n} onDismiss={onDismiss} />
        </div>
      ))}
    </div>
  );
}

// ── Bell button + dropdown panel ──────────────────────────────────────────

interface NotificationCenterProps {
  notifications: AppNotification[];
  unreadCount: number;
  onMarkRead: (id: string) => void;
  onMarkAllRead: () => void;
  onDismiss: (id: string) => void;
  onClearAll: () => void;
}

export function NotificationCenter({
  notifications,
  unreadCount,
  onMarkRead,
  onMarkAllRead,
  onDismiss,
  onClearAll,
}: NotificationCenterProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  // Mark visible as read when panel opens
  useEffect(() => {
    if (open) {
      notifications.filter((n) => !n.read).forEach((n) => onMarkRead(n.id));
    }
  }, [open, notifications, onMarkRead]);

  return (
    <div ref={ref} className="relative">
      {/* Bell button */}
      <button
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "relative flex h-8 w-8 items-center justify-center rounded-lg transition-colors",
          "text-muted-foreground hover:text-foreground hover:bg-muted/50",
          open && "bg-muted/50 text-foreground",
        )}
        aria-label="Notifications"
      >
        <Bell className="h-4 w-4" />
        {unreadCount > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-primary text-[10px] font-bold text-primary-foreground">
            {unreadCount > 9 ? "9+" : unreadCount}
          </span>
        )}
      </button>

      {/* Dropdown panel */}
      {open && (
        <div
          className={cn(
            "absolute right-0 top-10 z-50 w-80 rounded-xl border border-border/60 bg-background/95",
            "shadow-2xl backdrop-blur-sm",
            "animate-in fade-in slide-in-from-top-2 duration-150",
          )}
        >
          {/* Header */}
          <div className="flex items-center justify-between border-b px-4 py-3">
            <div className="flex items-center gap-2">
              <Bell className="h-4 w-4 text-muted-foreground" />
              <span className="text-sm font-semibold">Notifications</span>
              {notifications.length > 0 && (
                <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                  {notifications.length}
                </span>
              )}
            </div>
            <div className="flex items-center gap-1">
              {notifications.length > 0 && (
                <>
                  <Button variant="ghost" size="icon" className="h-6 w-6" onClick={onMarkAllRead} title="Mark all read" aria-label="Mark all notifications read">
                    <CheckCheck className="h-3.5 w-3.5" />
                  </Button>
                  <Button variant="ghost" size="icon" className="h-6 w-6" onClick={onClearAll} title="Clear all" aria-label="Clear notification history">
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </>
              )}
            </div>
          </div>

          {/* List */}
          <div className="max-h-96 overflow-y-auto">
            {notifications.length === 0 ? (
              <div className="flex flex-col items-center justify-center gap-2 py-10 text-muted-foreground">
                <Bell className="h-8 w-8 opacity-20" />
                <p className="text-xs">No notifications</p>
              </div>
            ) : (
              <div className="divide-y divide-border/40">
                {notifications.map((n) => {
                  const cfg = LEVEL_CONFIG[n.level];
                  const Icon = cfg.icon;
                  return (
                    <div
                      key={n.id}
                      className={cn(
                        "relative flex items-start gap-3 px-4 py-3 transition-colors hover:bg-muted/30",
                        !n.read && "bg-muted/20",
                      )}
                    >
                      {!n.read && (
                        <div className="absolute left-1.5 top-4 h-1.5 w-1.5 rounded-full bg-primary" />
                      )}
                      <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", cfg.iconClass)} />
                      <div className="flex-1 min-w-0">
                        <p className="text-xs font-semibold">{n.title}</p>
                        <p className="mt-0.5 text-xs text-muted-foreground leading-snug">{n.message}</p>
                        {n.actionNeeded && (
                          <Button
                            size="sm"
                            className="mt-2 h-6 px-2 text-[11px]"
                            onClick={() => void dispatchActionNeeded(n.actionNeeded!)}
                          >
                            {n.actionNeeded.action.label}
                          </Button>
                        )}
                        <p className="mt-1 text-[10px] text-muted-foreground/60">{timeAgo(n.timestamp)}</p>
                      </div>
                      <button
                        onClick={() => onDismiss(n.id)}
                        className="shrink-0 text-muted-foreground/40 hover:text-muted-foreground transition-colors"
                        aria-label={`Dismiss ${n.title}`}
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
