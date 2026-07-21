import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { engine } from "@/lib/api";
import { loadSettings, type AppSettings } from "@/lib/settings";
import {
  actionNeededStore,
  useActionNeeded,
  type ActionNeeded,
} from "@/features/action-needed";

export type NotificationLevel = "info" | "success" | "warning" | "error";

export interface AppNotification {
  id: string;
  title: string;
  message: string;
  level: NotificationLevel;
  timestamp: number;
  read: boolean;
  actionNeeded?: ActionNeeded;
}

/** A toast dismissal applies to one observation, not its stable requirement. */
export function notificationToastKey(notification: AppNotification): string {
  return notification.actionNeeded
    ? `${notification.id}:${notification.actionNeeded.observed_at ?? notification.timestamp}`
    : notification.id;
}

// Programmatically generated tones via Web Audio API — no asset files needed
function playTone(type: "chime" | "alert" | "error" | "success"): void {
  try {
    const ctx = new AudioContext();
    const gain = ctx.createGain();
    gain.connect(ctx.destination);

    const configs: Record<
      "chime" | "alert" | "success" | "error",
      { freq: number[]; dur: number; type: OscillatorType }
    > = {
      chime: { freq: [880, 1100], dur: 0.25, type: "sine" },
      alert: { freq: [440, 660], dur: 0.3, type: "triangle" },
      success: { freq: [523, 659, 784], dur: 0.18, type: "sine" },
      error: { freq: [220, 180], dur: 0.4, type: "sawtooth" },
    };

    const cfg = configs[type];
    let start = ctx.currentTime;

    cfg.freq.forEach((freq) => {
      const osc = ctx.createOscillator();
      osc.type = cfg.type;
      osc.frequency.setValueAtTime(freq, start);
      osc.connect(gain);
      gain.gain.setValueAtTime(0.18, start);
      gain.gain.exponentialRampToValueAtTime(0.001, start + cfg.dur);
      osc.start(start);
      osc.stop(start + cfg.dur);
      start += cfg.dur * 0.6;
    });

    // Clean up context after tones finish
    setTimeout(() => ctx.close(), (start + 0.5) * 1000);
  } catch {
    // AudioContext not available (e.g. no user gesture yet) — silent fail
  }
}

function soundForLevel(
  level: NotificationLevel,
): "chime" | "alert" | "error" | "success" {
  switch (level) {
    case "success":
      return "success";
    case "warning":
      return "alert";
    case "error":
      return "error";
    default:
      return "chime";
  }
}

let _notificationCounter = 0;

export function useNotifications() {
  const [baseNotifications, setBaseNotifications] = useState<AppNotification[]>(
    [],
  );
  const actionNeeded = useActionNeeded();
  const [actionReadVersions, setActionReadVersions] = useState<
    Map<string, number>
  >(() => new Map());
  const [hiddenActionVersions, setHiddenActionVersions] = useState<
    Map<string, number>
  >(() => new Map());
  // Toasts that have been hidden (auto-dismiss timer or toast X). Hiding a
  // toast must NOT delete the notification — it stays in the bell history.
  const [hiddenToastIds, setHiddenToastIds] = useState<Set<string>>(
    () => new Set(),
  );
  const soundEnabledRef = useRef(true);
  const soundStyleRef = useRef<AppSettings["notificationSoundStyle"]>("chime");

  // Load sound preferences from settings, and reload whenever any settings change.
  useEffect(() => {
    const reload = () => {
      loadSettings().then((s) => {
        soundEnabledRef.current = s.notificationSound !== false;
        soundStyleRef.current = s.notificationSoundStyle ?? "chime";
      });
    };
    reload();
    window.addEventListener("matrx-settings-changed", reload);
    return () => window.removeEventListener("matrx-settings-changed", reload);
  }, []);

  const addNotification = useCallback(
    (
      title: string,
      message: string,
      level: NotificationLevel = "info",
      timestamp?: number,
    ) => {
      const notif: AppNotification = {
        id: `notif-${Date.now()}-${++_notificationCounter}`,
        title,
        message,
        level,
        timestamp: timestamp ?? Date.now(),
        read: false,
      };

      setBaseNotifications((prev) => [notif, ...prev].slice(0, 100));

      if (soundEnabledRef.current) {
        // Always honour the user's explicitly chosen sound style.
        // Only fall back to level-based sounds if no style is set (null/undefined).
        const style = soundStyleRef.current ?? soundForLevel(level);
        playTone(style);
      }
    },
    [],
  );

  const markRead = useCallback(
    (id: string) => {
      if (id.startsWith("action:")) {
        const fingerprint = id.slice("action:".length);
        const item = actionNeeded.find(
          (candidate) => candidate.fingerprint === fingerprint,
        );
        if (item) {
          setActionReadVersions((prev) =>
            new Map(prev).set(fingerprint, item.observed_at ?? 0),
          );
        }
        return;
      }
      setBaseNotifications((prev) =>
        prev.map((n) => (n.id === id ? { ...n, read: true } : n)),
      );
    },
    [actionNeeded],
  );

  const markAllRead = useCallback(() => {
    setBaseNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
    setActionReadVersions(
      new Map(
        actionNeeded.map((item) => [item.fingerprint, item.observed_at ?? 0]),
      ),
    );
  }, [actionNeeded]);

  const dismiss = useCallback(
    (id: string) => {
      if (id.startsWith("action:")) {
        const fingerprint = id.slice("action:".length);
        const item = actionNeeded.find(
          (candidate) => candidate.fingerprint === fingerprint,
        );
        if (item) {
          actionNeededStore.resolve(fingerprint, item.observed_at ?? undefined);
          setHiddenActionVersions((prev) =>
            new Map(prev).set(fingerprint, item.observed_at ?? 0),
          );
        }
        return;
      }
      setBaseNotifications((prev) => prev.filter((n) => n.id !== id));
    },
    [actionNeeded],
  );

  const clearAll = useCallback(() => {
    setBaseNotifications([]);
    setHiddenToastIds(new Set());
    setHiddenActionVersions(
      new Map(
        actionNeeded.map((item) => [item.fingerprint, item.observed_at ?? 0]),
      ),
    );
  }, [actionNeeded]);

  const actionNotifications = useMemo<AppNotification[]>(
    () =>
      actionNeeded
        .filter(
          (item) =>
            (hiddenActionVersions.get(item.fingerprint) ?? -1) <
            (item.observed_at ?? 0),
        )
        .map((item) => {
          const observed = item.observed_at ?? Date.now();
          const timestamp =
            observed < 10_000_000_000 ? observed * 1000 : observed;
          return {
            id: `action:${item.fingerprint}`,
            title: item.title,
            message: item.message,
            level: "warning" as const,
            timestamp,
            read: (actionReadVersions.get(item.fingerprint) ?? -1) >= observed,
            actionNeeded: item,
          };
        }),
    [actionNeeded, actionReadVersions, hiddenActionVersions],
  );

  const notifications = useMemo(
    () =>
      [...actionNotifications, ...baseNotifications]
        .sort((a, b) => b.timestamp - a.timestamp)
        .slice(0, 100),
    [actionNotifications, baseNotifications],
  );

  /** Hide a toast popup without deleting the notification from history. */
  const hideToast = useCallback(
    (id: string) => {
      const notification = notifications.find(
        (candidate) => candidate.id === id,
      );
      if (!notification) return;
      setHiddenToastIds((prev) => {
        const next = new Set(prev);
        next.add(notificationToastKey(notification));
        return next;
      });
    },
    [notifications],
  );

  const unreadCount = notifications.filter((n) => !n.read).length;

  const toasts = useMemo(
    () =>
      notifications
        .filter(
          (notification) =>
            !hiddenToastIds.has(notificationToastKey(notification)),
        )
        .slice(0, 3),
    [notifications, hiddenToastIds],
  );

  // Listen for 'notification' events from the WebSocket
  useEffect(() => {
    const off = engine.on("message", (data: unknown) => {
      const msg = data as {
        type?: string;
        title?: string;
        message?: string;
        level?: string;
        timestamp?: number;
      };
      if (msg.type === "notification" && msg.title && msg.message) {
        addNotification(
          msg.title,
          msg.message,
          (msg.level as NotificationLevel) ?? "info",
          msg.timestamp,
        );
      }
    });
    return off;
  }, [addNotification]);

  const setSoundEnabled = useCallback((v: boolean) => {
    soundEnabledRef.current = v;
  }, []);

  return useMemo(
    () => ({
      notifications,
      toasts,
      unreadCount,
      addNotification,
      markRead,
      markAllRead,
      dismiss,
      hideToast,
      clearAll,
      setSoundEnabled,
    }),
    [
      notifications,
      toasts,
      unreadCount,
      addNotification,
      markRead,
      markAllRead,
      dismiss,
      hideToast,
      clearAll,
      setSoundEnabled,
    ],
  );
}
