import { MediaThumb } from "@/components/media/MediaThumb";
import { useState, useEffect, useCallback, useMemo } from "react";
import { useLocation } from "react-router-dom";
import {
  Mic,
  Camera,
  Bluetooth,
  Wifi,
  Monitor,
  Network,
  Shield,
  Usb,
  RefreshCw,
  CheckCircle2,
  XCircle,
  AlertCircle,
  HelpCircle,
  Loader2,
  Cpu,
  Eye,
  Volume2,
  MapPin,
  Download,
  Lock,
  Unlock,
  Battery,
  ChevronDown,
  ChevronRight,
  Image as ImageIcon,
  Video,
  Scan,
  Zap,
  ExternalLink,
} from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { engine } from "@/lib/api";
import type { PermissionInfo, PermissionStatusValue } from "@/lib/api";
import type { EngineStatus } from "@/hooks/use-engine";
import { PLUGIN_KEYS, type PermissionKey } from "@/hooks/use-permissions";
import { usePermissionsContext } from "@/contexts/PermissionsContext";
import { useAudioDevices } from "@/contexts/AudioDevicesContext";
import type { AudioDeviceInfo } from "@/lib/transcription/types";
import { PLATFORM } from "@/lib/platformCtx";
import { logWarn } from "@/lib/error-reporting";
import {
  actionNeededFromPermission,
  actionNeededStore,
} from "@/features/action-needed";

const TRIGGER_REQUIRED_KEYS = new Set<string>([
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

interface DevicesProps {
  engineStatus: EngineStatus;
  engineUrl: string | null;
}

const STATUS_CONFIG: Record<
  PermissionStatusValue,
  { icon: React.ReactNode; color: string; bgColor: string; label: string }
> = {
  granted: {
    icon: <CheckCircle2 className="h-4 w-4" />,
    color: "text-emerald-500",
    bgColor: "bg-emerald-500/10 border-emerald-500/20",
    label: "Granted",
  },
  denied: {
    icon: <XCircle className="h-4 w-4" />,
    color: "text-red-500",
    bgColor: "bg-red-500/10 border-red-500/20",
    label: "Denied",
  },
  not_determined: {
    icon: <AlertCircle className="h-4 w-4" />,
    color: "text-amber-500",
    bgColor: "bg-amber-500/10 border-amber-500/20",
    label: "Not Set",
  },
  restricted: {
    icon: <XCircle className="h-4 w-4" />,
    color: "text-red-400",
    bgColor: "bg-red-500/10 border-red-400/20",
    label: "Restricted",
  },
  unavailable: {
    icon: <HelpCircle className="h-4 w-4" />,
    color: "text-zinc-500",
    bgColor: "bg-zinc-500/10 border-zinc-500/20",
    label: "Unavailable",
  },
  unknown: {
    icon: <HelpCircle className="h-4 w-4" />,
    color: "text-zinc-400",
    bgColor: "bg-zinc-500/10 border-zinc-400/20",
    label: "Unknown",
  },
};

function StatusBadge({ status }: { status: PermissionStatusValue }) {
  const cfg = STATUS_CONFIG[status] ?? STATUS_CONFIG.unknown;
  return (
    <Badge
      variant="outline"
      className={`gap-1 text-[10px] px-1.5 py-0 ${cfg.color} ${cfg.bgColor}`}
    >
      {cfg.icon}
      {cfg.label}
    </Badge>
  );
}

function SectionCard({
  icon,
  title,
  status,
  description,
  children,
  defaultOpen = false,
}: {
  icon: React.ReactNode;
  title: string;
  status: PermissionStatusValue;
  description: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  useEffect(() => {
    if (defaultOpen) setOpen(true);
  }, [defaultOpen]);
  const cfg = STATUS_CONFIG[status] ?? STATUS_CONFIG.unknown;

  return (
    <Card
      className={`transition-all ${
        status === "denied"
          ? "border-red-500/20"
          : status === "granted"
            ? "border-emerald-500/10"
            : ""
      }`}
    >
      <CardContent className="p-0">
        <button
          className="flex w-full items-center gap-4 p-4 text-left"
          onClick={() => setOpen((v) => !v)}
        >
          <div
            className={`flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg ${cfg.bgColor}`}
          >
            <span className={cfg.color}>{icon}</span>
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-medium">{title}</h3>
              <StatusBadge status={status} />
            </div>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {description}
            </p>
          </div>
          <span className="flex-shrink-0 text-muted-foreground">
            {open ? (
              <ChevronDown className="h-4 w-4" />
            ) : (
              <ChevronRight className="h-4 w-4" />
            )}
          </span>
        </button>
        {open && (
          <div className="border-t bg-muted/20 px-4 py-4">{children}</div>
        )}
      </CardContent>
    </Card>
  );
}

function PermissionAlert({ perm }: { perm: PermissionInfo | null }) {
  const { request } = usePermissionsContext();
  const actionNeeded = useMemo(
    () =>
      perm
        ? actionNeededFromPermission({
            permission: perm.permission,
            status: perm.status,
            feature: `Devices: ${perm.permission.replace(/_/g, " ")}`,
            source: `devices-permission:${perm.permission}`,
          })
        : null,
    [perm?.permission, perm?.status],
  );
  useEffect(() => {
    if (!perm) return;
    const source = `devices-permission:${perm.permission}`;
    actionNeededStore.reconcileLocal(source, actionNeeded ? [actionNeeded] : null);
  }, [actionNeeded, perm?.permission]);
  if (!perm || perm.status === "granted") return null;

  const isTriggerRequired =
    PLATFORM.is_mac &&
    perm.status === "not_determined" &&
    TRIGGER_REQUIRED_KEYS.has(perm.permission);

  return (
    <div className="mb-4 flex items-start gap-2 rounded-lg border border-amber-500/20 bg-amber-500/5 p-3">
      <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0 text-amber-500" />
      <div className="flex-1 min-w-0">
        <p className="text-xs font-medium text-amber-500">
          Permission Required
        </p>
        {perm.user_instructions && (
          <p className="mt-0.5 text-xs text-muted-foreground">
            {perm.user_instructions}
          </p>
        )}
        {perm.grant_instructions && (
          <p className="mt-1 font-mono text-[10px] text-muted-foreground/70">
            {perm.grant_instructions}
          </p>
        )}
      </div>
      <button
        onClick={() => void request(perm.permission as PermissionKey)}
        className="shrink-0 flex items-center gap-1 rounded px-2 py-1 text-[11px] font-medium text-amber-500 border border-amber-500/30 hover:bg-amber-500/10 transition-colors"
      >
        {isTriggerRequired ? (
          <AlertCircle className="h-3 w-3" />
        ) : (
          <ExternalLink className="h-3 w-3" />
        )}
        {isTriggerRequired ? "Request Access" : "Open Settings"}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Microphone Card
// ---------------------------------------------------------------------------

function MicrophoneCard({ perm }: { perm: PermissionInfo | null }) {
  // Use the shared audio devices context — same source of truth as Voice page.
  // Previously this called engine.getAudioDevices() (Python REST), which failed
  // when the engine wasn't up, while Voice used Tauri IPC which always works.
  const {
    audioDevices,
    selectedDevice,
    isLoading: loadingDevices,
    listAudioDevices,
    setSelectedDevice,
  } = useAudioDevices();

  const [duration, setDuration] = useState(5);
  const [recording, setRecording] = useState(false);
  const [audioB64, setAudioB64] = useState<string | null>(null);
  const [audioMime, setAudioMime] = useState("audio/wav");
  const [error, setError] = useState<string | null>(null);

  const handleRecord = async () => {
    setError(null);
    setAudioB64(null);
    setRecording(true);
    try {
      // Test recording uses the system default or first available device.
      // The selectedDevice preference is for transcription (set on the Voice page).
      const result = await engine.recordAudio({
        duration_seconds: duration,
      });
      const meta = result.metadata as Record<string, unknown> | null;
      if (meta?.base64) {
        setAudioB64(String(meta.base64));
        setAudioMime(String(meta.mime ?? "audio/wav"));
      } else {
        setError(result.output || "Recording failed");
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setRecording(false);
    }
  };

  const audioSrc = audioB64 ? `data:${audioMime};base64,${audioB64}` : null;

  return (
    <div className="space-y-4">
      <PermissionAlert perm={perm} />

      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={listAudioDevices}
          disabled={loadingDevices}
        >
          {loadingDevices ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" />
          )}
          Scan Devices
        </Button>
        {selectedDevice && (
          <span className="text-xs text-primary font-medium">
            Active: {selectedDevice}
          </span>
        )}
      </div>

      {audioDevices.length > 0 && (
        <div className="space-y-3">
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {audioDevices.map((dev: AudioDeviceInfo) => {
              const isSelected = selectedDevice === dev.name;
              const isDefault = !selectedDevice && dev.is_default;
              return (
                <button
                  key={dev.name}
                  onClick={() =>
                    setSelectedDevice(isSelected ? null : dev.name)
                  }
                  className={`flex items-center gap-3 rounded-lg border px-3 py-2.5 text-left transition-all hover:border-primary/40 ${
                    isSelected || isDefault
                      ? "border-primary bg-primary/5"
                      : "border-border bg-background"
                  }`}
                >
                  <Mic
                    className={`h-4 w-4 flex-shrink-0 ${isSelected || isDefault ? "text-primary" : "text-muted-foreground"}`}
                  />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-xs font-medium">{dev.name}</p>
                    <p className="text-[10px] text-muted-foreground">
                      {dev.channels.length > 0
                        ? `${Math.max(...dev.channels)}ch`
                        : ""}
                      {dev.sample_rates.length > 0
                        ? ` · ${Math.round(Math.max(...dev.sample_rates) / 1000)}kHz`
                        : ""}
                      {dev.is_default ? " · default" : ""}
                    </p>
                  </div>
                  {(isSelected || isDefault) && (
                    <CheckCircle2 className="h-3.5 w-3.5 flex-shrink-0 text-primary" />
                  )}
                </button>
              );
            })}
          </div>

          <div className="flex items-center gap-3">
            <span className="text-xs text-muted-foreground">Duration:</span>
            {[3, 5, 10, 30].map((d) => (
              <button
                key={d}
                onClick={() => setDuration(d)}
                className={`rounded-md px-2.5 py-1 text-xs transition-all ${
                  duration === d
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted text-muted-foreground hover:bg-muted/80"
                }`}
              >
                {d}s
              </button>
            ))}
          </div>

          <div className="flex items-center gap-2">
            <Button
              onClick={handleRecord}
              disabled={recording}
              size="sm"
              className={recording ? "bg-red-500 hover:bg-red-600" : ""}
            >
              {recording ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  Recording {duration}s…
                </>
              ) : (
                <>
                  <Mic className="h-3.5 w-3.5" />
                  Record {duration}s
                </>
              )}
            </Button>
          </div>
        </div>
      )}

      {audioDevices.length === 0 && !loadingDevices && (
        <p className="text-xs text-muted-foreground">
          No microphones detected. Grant microphone permission and click Scan
          Devices.
        </p>
      )}

      {error && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-3 text-xs text-red-400">
          {error}
        </div>
      )}

      {audioSrc && (
        <div className="rounded-lg border bg-background p-3">
          <p className="mb-2 text-xs font-medium text-emerald-500">
            Recording Complete — Play it back:
          </p>
          <audio controls src={audioSrc} className="w-full" />
          <a
            href={audioSrc}
            download="recording.wav"
            className="mt-2 flex items-center gap-1.5 text-[10px] text-muted-foreground hover:text-foreground transition-colors"
          >
            <Download className="h-3 w-3" />
            Download WAV
          </a>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Speakers Card
// ---------------------------------------------------------------------------

function SpeakersCard({ perm }: { perm: PermissionInfo | null }) {
  const [outputs, setOutputs] = useState<Array<Record<string, unknown>>>([]);
  const [loading, setLoading] = useState(false);

  const loadDevices = useCallback(async () => {
    setLoading(true);
    try {
      const result = await engine.getAudioDevices();
      const meta = result.metadata as Record<string, unknown> | null;
      setOutputs((meta?.outputs as Array<Record<string, unknown>>) ?? []);
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadDevices();
  }, []);

  return (
    <div className="space-y-4">
      <PermissionAlert perm={perm} />
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={loadDevices}
          disabled={loading}
        >
          {loading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" />
          )}
          Scan Devices
        </Button>
      </div>
      {outputs.length > 0 ? (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {outputs.map((dev, i) => (
            <div
              key={i}
              className="flex items-center gap-3 rounded-lg border border-border bg-background px-3 py-2.5"
            >
              <Volume2 className="h-4 w-4 flex-shrink-0 text-muted-foreground" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs font-medium">
                  {String(dev.name)}
                </p>
                <p className="text-[10px] text-muted-foreground">
                  {dev.channels ? `${dev.channels}ch` : ""}{" "}
                  {dev.sample_rate
                    ? `${Math.round(Number(dev.sample_rate) / 1000)}kHz`
                    : ""}
                </p>
              </div>
            </div>
          ))}
        </div>
      ) : !loading ? (
        <p className="text-xs text-muted-foreground">
          No output devices detected.
        </p>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Camera Card
// ---------------------------------------------------------------------------

function CameraCard({ perm }: { perm: PermissionInfo | null }) {
  const [cameras, setCameras] = useState<Array<Record<string, unknown>>>([]);
  const [selectedCamera, setSelectedCamera] = useState<number | null>(null);
  const [mode, setMode] = useState<"photo" | "video">("photo");
  const [duration, setDuration] = useState(5);
  const [loading, setLoading] = useState(false);
  const [mediaB64, setMediaB64] = useState<string | null>(null);
  const [mediaMime, setMediaMime] = useState("image/jpeg");
  const [error, setError] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);

  const loadCameras = useCallback(async () => {
    setScanning(true);
    try {
      const result = await engine.getCameraDevices();
      const meta = result.metadata as Record<string, unknown> | null;
      const devs = (meta?.devices as Array<Record<string, unknown>>) ?? [];
      setCameras(devs);
      if (devs.length > 0) setSelectedCamera(0);
    } catch (e) {
      setError(String(e));
    } finally {
      setScanning(false);
    }
  }, []);

  useEffect(() => {
    loadCameras();
  }, []);

  const handleCapture = async () => {
    setError(null);
    setMediaB64(null);
    setLoading(true);
    try {
      if (mode === "photo") {
        const result = await engine.capturePhoto({
          device_index: selectedCamera ?? 0,
        });
        const meta = result.metadata as Record<string, unknown> | null;
        if (meta?.base64) {
          setMediaB64(String(meta.base64));
          setMediaMime(String(meta.mime ?? "image/jpeg"));
        } else {
          setError(result.output || "Capture failed");
        }
      } else {
        const result = await engine.recordVideo({
          device_index: selectedCamera ?? 0,
          duration_seconds: duration,
        });
        const meta = result.metadata as Record<string, unknown> | null;
        if (meta?.base64) {
          setMediaB64(String(meta.base64));
          setMediaMime(String(meta.mime ?? "video/mp4"));
        } else {
          setError(result.output || "Recording failed");
        }
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  const mediaSrc = mediaB64 ? `data:${mediaMime};base64,${mediaB64}` : null;

  return (
    <div className="space-y-4">
      <PermissionAlert perm={perm} />

      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={loadCameras}
          disabled={scanning}
        >
          {scanning ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Scan className="h-3.5 w-3.5" />
          )}
          Scan Cameras
        </Button>
      </div>

      {cameras.length > 0 && (
        <div className="space-y-3">
          <div className="flex flex-wrap gap-2">
            {cameras.map((cam, i) => (
              <button
                key={i}
                onClick={() => setSelectedCamera(i)}
                className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-xs transition-all ${
                  selectedCamera === i
                    ? "border-primary bg-primary/5 font-medium"
                    : "border-border bg-background hover:border-primary/40"
                }`}
              >
                <Camera className="h-3.5 w-3.5" />
                {String(cam.name)}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-1 rounded-lg border p-1 w-fit">
            <button
              onClick={() => setMode("photo")}
              className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs transition-all ${
                mode === "photo"
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <ImageIcon className="h-3.5 w-3.5" />
              Photo
            </button>
            <button
              onClick={() => setMode("video")}
              className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs transition-all ${
                mode === "video"
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <Video className="h-3.5 w-3.5" />
              Video
            </button>
          </div>

          {mode === "video" && (
            <div className="flex items-center gap-3">
              <span className="text-xs text-muted-foreground">Duration:</span>
              {[3, 5, 10].map((d) => (
                <button
                  key={d}
                  onClick={() => setDuration(d)}
                  className={`rounded-md px-2.5 py-1 text-xs transition-all ${
                    duration === d
                      ? "bg-primary text-primary-foreground"
                      : "bg-muted text-muted-foreground hover:bg-muted/80"
                  }`}
                >
                  {d}s
                </button>
              ))}
            </div>
          )}

          <Button
            onClick={handleCapture}
            disabled={loading || selectedCamera === null}
            size="sm"
          >
            {loading ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                {mode === "photo" ? "Capturing…" : `Recording ${duration}s…`}
              </>
            ) : (
              <>
                {mode === "photo" ? (
                  <Camera className="h-3.5 w-3.5" />
                ) : (
                  <Video className="h-3.5 w-3.5" />
                )}
                {mode === "photo" ? "Take Photo" : `Record ${duration}s`}
              </>
            )}
          </Button>
        </div>
      )}

      {cameras.length === 0 && !scanning && (
        <p className="text-xs text-muted-foreground">
          No cameras found. Grant camera permission and click Scan Cameras.
        </p>
      )}

      {error && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-3 text-xs text-red-400">
          {error}
        </div>
      )}

      {mediaSrc && (
        <div className="rounded-lg border bg-background p-3">
          <p className="mb-2 text-xs font-medium text-emerald-500">
            {mode === "photo" ? "Photo Captured" : "Video Recorded"}
          </p>
          {mediaMime.startsWith("image/") ? (
            <MediaThumb
              item={{
                id: "device-capture",
                kind: "image",
                url: mediaSrc,
                itemId: null,
                source: "result",
                fileName: "capture.png",
              }}
              variant="gallery"
              className="max-h-64 w-full rounded-md [&_img]:max-h-64 [&_img]:object-contain"
            />
          ) : (
            <video
              controls
              src={mediaSrc}
              className="max-h-64 w-full rounded-md"
            />
          )}
          <a
            href={mediaSrc}
            download={mode === "photo" ? "photo.jpg" : "video.mp4"}
            className="mt-2 flex items-center gap-1.5 text-[10px] text-muted-foreground hover:text-foreground transition-colors"
          >
            <Download className="h-3 w-3" />
            Download
          </a>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Screen Recording Card
// ---------------------------------------------------------------------------

function ScreenRecordingCard({ perm }: { perm: PermissionInfo | null }) {
  const [monitors, setMonitors] = useState<Array<Record<string, unknown>>>([]);
  const [selectedMonitor, setSelectedMonitor] = useState<string>("all");
  const [mode, setMode] = useState<"screenshot" | "video">("screenshot");
  const [duration, setDuration] = useState(5);
  const [loading, setLoading] = useState(false);
  const [mediaB64, setMediaB64] = useState<string | null>(null);
  const [mediaMime, setMediaMime] = useState("image/png");
  const [error, setError] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);

  const loadScreens = useCallback(async () => {
    setScanning(true);
    try {
      const result = await engine.getScreens();
      const meta = result.metadata as Record<string, unknown> | null;
      const mons = (meta?.monitors as Array<Record<string, unknown>>) ?? [];
      setMonitors(mons);
    } catch (e) {
      setError(String(e));
    } finally {
      setScanning(false);
    }
  }, []);

  useEffect(() => {
    loadScreens();
  }, []);

  const handleCapture = async () => {
    setError(null);
    setMediaB64(null);
    setLoading(true);
    try {
      if (mode === "screenshot") {
        const result = await engine.takeScreenshot(selectedMonitor);
        const meta = result.metadata as Record<string, unknown> | null;
        if (meta?.base64) {
          setMediaB64(String(meta.base64));
          setMediaMime(String(meta.mime ?? "image/png"));
        } else {
          setError(result.output || "Screenshot failed");
        }
      } else {
        const screenIdx =
          selectedMonitor === "all" ? undefined : parseInt(selectedMonitor);
        const result = await engine.recordScreen({
          ...(isNaN(screenIdx as number) ? {} : { screen_index: screenIdx as number }),
          duration_seconds: duration,
        });
        const meta = result.metadata as Record<string, unknown> | null;
        if (meta?.base64) {
          setMediaB64(String(meta.base64));
          setMediaMime(String(meta.mime ?? "video/mp4"));
        } else {
          setError(result.output || "Screen recording failed");
        }
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  const mediaSrc = mediaB64 ? `data:${mediaMime};base64,${mediaB64}` : null;
  const ext =
    mediaMime === "image/gif"
      ? "gif"
      : mediaMime.startsWith("video")
        ? "mp4"
        : "png";

  return (
    <div className="space-y-4">
      <PermissionAlert perm={perm} />

      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={loadScreens}
          disabled={scanning}
        >
          {scanning ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Monitor className="h-3.5 w-3.5" />
          )}
          Detect Screens
        </Button>
      </div>

      {monitors.length > 0 && (
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => setSelectedMonitor("all")}
            className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-xs transition-all ${
              selectedMonitor === "all"
                ? "border-primary bg-primary/5 font-medium"
                : "border-border bg-background hover:border-primary/40"
            }`}
          >
            <Monitor className="h-3.5 w-3.5" />
            All Screens
          </button>
          {monitors.map((m) => (
            <button
              key={String(m.index)}
              onClick={() => setSelectedMonitor(String(m.index))}
              className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-xs transition-all ${
                selectedMonitor === String(m.index)
                  ? "border-primary bg-primary/5 font-medium"
                  : "border-border bg-background hover:border-primary/40"
              }`}
            >
              <Monitor className="h-3.5 w-3.5" />
              {String(m.name ?? `Monitor ${m.index}`)}
              <span className="text-[10px] text-muted-foreground">
                {String(m.width ?? "")}×{String(m.height ?? "")}
                {m.is_primary ? " (primary)" : ""}
              </span>
            </button>
          ))}
        </div>
      )}

      <div className="flex items-center gap-1 rounded-lg border p-1 w-fit">
        <button
          onClick={() => setMode("screenshot")}
          className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs transition-all ${
            mode === "screenshot"
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:text-foreground"
          }`}
        >
          <ImageIcon className="h-3.5 w-3.5" />
          Screenshot
        </button>
        <button
          onClick={() => setMode("video")}
          className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs transition-all ${
            mode === "video"
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:text-foreground"
          }`}
        >
          <Video className="h-3.5 w-3.5" />
          Screen Record
        </button>
      </div>

      {mode === "video" && (
        <div className="flex items-center gap-3">
          <span className="text-xs text-muted-foreground">Duration:</span>
          {[3, 5, 10, 30].map((d) => (
            <button
              key={d}
              onClick={() => setDuration(d)}
              className={`rounded-md px-2.5 py-1 text-xs transition-all ${
                duration === d
                  ? "bg-primary text-primary-foreground"
                  : "bg-muted text-muted-foreground hover:bg-muted/80"
              }`}
            >
              {d}s
            </button>
          ))}
        </div>
      )}

      <Button onClick={handleCapture} disabled={loading} size="sm">
        {loading ? (
          <>
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            {mode === "screenshot" ? "Capturing…" : `Recording ${duration}s…`}
          </>
        ) : (
          <>
            {mode === "screenshot" ? (
              <Eye className="h-3.5 w-3.5" />
            ) : (
              <Video className="h-3.5 w-3.5" />
            )}
            {mode === "screenshot" ? "Take Screenshot" : `Record ${duration}s`}
          </>
        )}
      </Button>

      {error && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-3 text-xs text-red-400">
          {error}
        </div>
      )}

      {mediaSrc && (
        <div className="rounded-lg border bg-background p-3">
          <p className="mb-2 text-xs font-medium text-emerald-500">
            {mode === "screenshot"
              ? "Screenshot Captured"
              : "Recording Complete"}
          </p>
          {mediaMime.startsWith("image/") ? (
            <MediaThumb
              item={{
                id: "device-screenshot",
                kind: "image",
                url: mediaSrc,
                itemId: null,
                source: "result",
                fileName: "screenshot.png",
              }}
              variant="gallery"
              className="max-h-72 w-full rounded-md border [&_img]:max-h-72 [&_img]:object-contain"
            />
          ) : (
            <video
              controls
              src={mediaSrc}
              className="max-h-72 w-full rounded-md"
            />
          )}
          <a
            href={mediaSrc}
            download={`screen.${ext}`}
            className="mt-2 flex items-center gap-1.5 text-[10px] text-muted-foreground hover:text-foreground transition-colors"
          >
            <Download className="h-3 w-3" />
            Download {ext.toUpperCase()}
          </a>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Bluetooth Card
// ---------------------------------------------------------------------------

function BluetoothCard({ perm }: { perm: PermissionInfo | null }) {
  const [devices, setDevices] = useState<Array<Record<string, unknown>>>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadDevices = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await engine.getBluetoothDevices();
      const meta = result.metadata as Record<string, unknown> | null;
      setDevices((meta?.devices as Array<Record<string, unknown>>) ?? []);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadDevices();
  }, []);

  const connected = devices.filter((d) => d.connected);
  const paired = devices.filter((d) => !d.connected);

  return (
    <div className="space-y-4">
      <PermissionAlert perm={perm} />

      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={loadDevices}
          disabled={loading}
        >
          {loading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" />
          )}
          Refresh
        </Button>
        <span className="text-xs text-muted-foreground">
          {devices.length} device(s) found
        </span>
      </div>

      {error && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-3 text-xs text-red-400">
          {error}
        </div>
      )}

      {[
        { label: "Connected", devs: connected, color: "text-emerald-500" },
        {
          label: "Paired / Available",
          devs: paired,
          color: "text-muted-foreground",
        },
      ].map(({ label, devs, color }) =>
        devs.length > 0 ? (
          <div key={label}>
            <p className={`mb-2 text-xs font-medium ${color}`}>{label}</p>
            <div className="overflow-hidden rounded-lg border">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b bg-muted/40">
                    <th className="px-3 py-2 text-left font-medium text-muted-foreground">
                      Name
                    </th>
                    <th className="px-3 py-2 text-left font-medium text-muted-foreground">
                      Type
                    </th>
                    <th className="px-3 py-2 text-left font-medium text-muted-foreground">
                      Address
                    </th>
                    <th className="px-3 py-2 text-left font-medium text-muted-foreground">
                      Battery
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {devs.map((dev, i) => (
                    <tr
                      key={i}
                      className="border-b last:border-0 hover:bg-muted/20"
                    >
                      <td className="px-3 py-2 font-medium">
                        {String(dev.name ?? "Unknown")}
                      </td>
                      <td className="px-3 py-2 text-muted-foreground">
                        {String(dev.type ?? dev.device_type ?? "—")}
                      </td>
                      <td className="px-3 py-2 font-mono text-[10px] text-muted-foreground">
                        {String(dev.address ?? "—")}
                      </td>
                      <td className="px-3 py-2">
                        {dev.battery ? (
                          <span className="flex items-center gap-1">
                            <Battery className="h-3 w-3" />
                            {String(dev.battery)}%
                          </span>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : null,
      )}

      {devices.length === 0 && !loading && (
        <p className="text-xs text-muted-foreground">
          No Bluetooth devices found. Enable Bluetooth and click Refresh.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// WiFi Card
// ---------------------------------------------------------------------------

function rssiToBars(rssi: number): string {
  if (rssi >= -50) return "▂▄▆█";
  if (rssi >= -60) return "▂▄▆_";
  if (rssi >= -70) return "▂▄__";
  if (rssi >= -80) return "▂___";
  return "____";
}

function WifiCard({ perm }: { perm: PermissionInfo | null }) {
  const [networks, setNetworks] = useState<Array<Record<string, unknown>>>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastScan, setLastScan] = useState<Date | null>(null);

  const loadNetworks = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await engine.getWifiNetworks();
      const meta = result.metadata as Record<string, unknown> | null;
      setNetworks((meta?.networks as Array<Record<string, unknown>>) ?? []);
      setLastScan(new Date());
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  // Pre-populate from permission devices if available
  useEffect(() => {
    const initialNetworks = perm?.devices ?? [];
    if (initialNetworks.length > 0) {
      setNetworks(initialNetworks as Array<Record<string, unknown>>);
    } else {
      loadNetworks();
    }
  }, []);

  const connectedNet = networks.find((n) => n.connected);
  const otherNets = networks.filter((n) => !n.connected);

  return (
    <div className="space-y-4">
      <PermissionAlert perm={perm} />

      <div className="flex items-center gap-3">
        <Button
          variant="outline"
          size="sm"
          onClick={loadNetworks}
          disabled={loading}
        >
          {loading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Wifi className="h-3.5 w-3.5" />
          )}
          Scan Networks
        </Button>
        {lastScan && (
          <span className="text-[10px] text-muted-foreground">
            Last scan:{" "}
            {lastScan.toLocaleTimeString([], {
              hour: "2-digit",
              minute: "2-digit",
              second: "2-digit",
            })}
          </span>
        )}
        <span className="text-xs text-muted-foreground">
          {networks.length} network(s)
        </span>
      </div>

      {error && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-3 text-xs text-red-400">
          {error}
        </div>
      )}

      {connectedNet && (
        <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-3">
          <p className="mb-1 text-[10px] font-medium uppercase tracking-wider text-emerald-500">
            Connected
          </p>
          <div className="flex items-center gap-3">
            <Wifi className="h-5 w-5 text-emerald-500" />
            <div>
              <p className="text-sm font-semibold">
                {String(connectedNet.ssid ?? "Unknown")}
              </p>
              <p className="text-[10px] text-muted-foreground">
                {connectedNet.rssi
                  ? `${connectedNet.rssi} dBm`
                  : connectedNet.signal_percent
                    ? `${connectedNet.signal_percent}%`
                    : ""}
                {connectedNet.channel
                  ? ` · Channel ${connectedNet.channel}`
                  : ""}
                {connectedNet.security ? ` · ${connectedNet.security}` : ""}
              </p>
            </div>
          </div>
        </div>
      )}

      {networks.length > 0 && (
        <div className="overflow-hidden rounded-lg border">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b bg-muted/40">
                <th className="px-3 py-2 text-left font-medium text-muted-foreground">
                  SSID
                </th>
                <th className="px-3 py-2 text-left font-medium text-muted-foreground">
                  Signal
                </th>
                <th className="px-3 py-2 text-left font-medium text-muted-foreground">
                  Channel
                </th>
                <th className="px-3 py-2 text-left font-medium text-muted-foreground">
                  Security
                </th>
              </tr>
            </thead>
            <tbody>
              {otherNets.map((net, i) => {
                const rssi = Number(net.rssi ?? 0);
                const sigPct = net.signal_percent
                  ? Number(net.signal_percent)
                  : null;
                return (
                  <tr
                    key={i}
                    className="border-b last:border-0 hover:bg-muted/20"
                  >
                    <td className="px-3 py-2 font-medium">
                      {String(net.ssid ?? "(hidden)")}
                    </td>
                    <td className="px-3 py-2 font-mono text-emerald-500">
                      {rssi ? rssiToBars(rssi) : sigPct ? `${sigPct}%` : "—"}
                      {rssi ? (
                        <span className="ml-1 text-[10px] text-muted-foreground">
                          {rssi} dBm
                        </span>
                      ) : null}
                    </td>
                    <td className="px-3 py-2 text-muted-foreground">
                      {String(net.channel ?? "—")}
                    </td>
                    <td className="px-3 py-2">
                      <span
                        className={`flex items-center gap-1 ${net.security && String(net.security).toLowerCase() !== "open" && String(net.security) ? "text-amber-500" : "text-zinc-500"}`}
                      >
                        {net.security &&
                        String(net.security).toLowerCase() !== "open" ? (
                          <Lock className="h-3 w-3" />
                        ) : (
                          <Unlock className="h-3 w-3" />
                        )}
                        {String(net.security || "Open")}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {networks.length === 0 && !loading && (
        <p className="text-xs text-muted-foreground">
          No networks found. Enable WiFi and click Scan Networks.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Network Interfaces Card
// ---------------------------------------------------------------------------

function NetworkCard({ perm }: { perm: PermissionInfo | null }) {
  const [interfaces, setInterfaces] = useState<Array<Record<string, unknown>>>(
    [],
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [internetOk, setInternetOk] = useState<boolean | null>(null);

  const loadInterfaces = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await engine.getNetworkInfo();
      const meta = result.metadata as Record<string, unknown> | null;
      const ifaces = (meta?.interfaces as Array<Record<string, unknown>>) ?? [];
      setInterfaces(ifaces);
      // Check for internet in output string
      if (result.output.includes("Internet reachable")) setInternetOk(true);
      else if (result.output.includes("unreachable")) setInternetOk(false);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Pre-populate from permission data
    const initial = perm?.devices ?? [];
    if (initial.length > 0) {
      setInterfaces(initial as Array<Record<string, unknown>>);
    } else {
      loadInterfaces();
    }
  }, []);

  const allIfaces = interfaces;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <Button
          variant="outline"
          size="sm"
          onClick={loadInterfaces}
          disabled={loading}
        >
          {loading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" />
          )}
          Refresh
        </Button>
        {internetOk !== null && (
          <Badge
            variant="outline"
            className={
              internetOk
                ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-500"
                : "border-red-500/30 bg-red-500/10 text-red-500"
            }
          >
            {internetOk ? (
              <CheckCircle2 className="h-3 w-3 mr-1" />
            ) : (
              <XCircle className="h-3 w-3 mr-1" />
            )}
            Internet {internetOk ? "reachable" : "unreachable"}
          </Badge>
        )}
      </div>

      {error && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-3 text-xs text-red-400">
          {error}
        </div>
      )}

      {allIfaces.length > 0 && (
        <div className="overflow-hidden rounded-lg border">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b bg-muted/40">
                <th className="px-3 py-2 text-left font-medium text-muted-foreground">
                  Interface
                </th>
                <th className="px-3 py-2 text-left font-medium text-muted-foreground">
                  Type
                </th>
                <th className="px-3 py-2 text-left font-medium text-muted-foreground">
                  IPv4
                </th>
                <th className="px-3 py-2 text-left font-medium text-muted-foreground">
                  MAC
                </th>
                <th className="px-3 py-2 text-left font-medium text-muted-foreground">
                  Status
                </th>
              </tr>
            </thead>
            <tbody>
              {allIfaces.map((iface, i) => (
                <tr
                  key={i}
                  className="border-b last:border-0 hover:bg-muted/20"
                >
                  <td className="px-3 py-2 font-mono font-medium">
                    {String(iface.name)}
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">
                    {String(iface.type ?? "—")}
                  </td>
                  <td className="px-3 py-2 font-mono">
                    {String(iface.ipv4 ?? "—")}
                  </td>
                  <td className="px-3 py-2 font-mono text-[10px] text-muted-foreground">
                    {String(iface.mac ?? "—")}
                  </td>
                  <td className="px-3 py-2">
                    <Badge
                      variant="outline"
                      className={`text-[10px] px-1 py-0 ${
                        iface.is_up
                          ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-500"
                          : "border-zinc-500/30 bg-zinc-500/10 text-zinc-500"
                      }`}
                    >
                      {iface.is_up ? "UP" : "DOWN"}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {allIfaces.length === 0 && !loading && (
        <p className="text-xs text-muted-foreground">
          No network interfaces detected.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Connected Devices Card
// ---------------------------------------------------------------------------

const CATEGORY_ICONS: Record<string, React.ReactNode> = {
  display: <Monitor className="h-4 w-4" />,
  usb: <Usb className="h-4 w-4" />,
  bluetooth: <Bluetooth className="h-4 w-4" />,
  input: <Zap className="h-4 w-4" />,
  storage: <Cpu className="h-4 w-4" />,
  camera: <Camera className="h-4 w-4" />,
  audio: <Volume2 className="h-4 w-4" />,
  network: <Network className="h-4 w-4" />,
  other: <Usb className="h-4 w-4" />,
};

function ConnectedDevicesCard() {
  const [devices, setDevices] = useState<Array<Record<string, unknown>>>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedCats, setExpandedCats] = useState<Set<string>>(
    new Set(["display"]),
  );

  const loadDevices = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await engine.getConnectedDevices();
      const meta = result.metadata as Record<string, unknown> | null;
      setDevices((meta?.devices as Array<Record<string, unknown>>) ?? []);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadDevices();
  }, []);

  const categories: Record<string, Array<Record<string, unknown>>> = {};
  for (const dev of devices) {
    const cat = String(dev.category ?? dev.type ?? "other").toLowerCase();
    categories[cat] = categories[cat] ?? [];
    categories[cat].push(dev);
  }

  const toggleCat = (cat: string) => {
    setExpandedCats((prev) => {
      const next = new Set(prev);
      if (next.has(cat)) next.delete(cat);
      else next.add(cat);
      return next;
    });
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={loadDevices}
          disabled={loading}
        >
          {loading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" />
          )}
          Refresh
        </Button>
        <span className="text-xs text-muted-foreground">
          {devices.length} device(s) found
        </span>
      </div>

      {error && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-3 text-xs text-red-400">
          {error}
        </div>
      )}

      {Object.entries(categories).map(([cat, devs]) => (
        <div key={cat} className="rounded-lg border overflow-hidden">
          <button
            className="flex w-full items-center gap-3 bg-muted/30 px-3 py-2.5 text-left hover:bg-muted/50 transition-colors"
            onClick={() => toggleCat(cat)}
          >
            <span className="text-muted-foreground">
              {CATEGORY_ICONS[cat] ?? CATEGORY_ICONS.other}
            </span>
            <span className="text-xs font-medium capitalize">{cat}</span>
            <Badge
              variant="outline"
              className="ml-auto text-[10px] px-1.5 py-0"
            >
              {devs.length}
            </Badge>
            {expandedCats.has(cat) ? (
              <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
            )}
          </button>
          {expandedCats.has(cat) && (
            <div className="divide-y">
              {devs.map((dev, i) => (
                <div key={i} className="flex items-start gap-3 px-4 py-3">
                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-medium">
                      {String(dev.name ?? "Unknown")}
                    </p>
                    <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
                      {!!dev.resolution && (
                        <span className="text-[10px] text-muted-foreground">
                          {String(dev.resolution)}
                        </span>
                      )}
                      {!!dev.connection && (
                        <span className="text-[10px] text-muted-foreground">
                          {String(dev.connection)}
                        </span>
                      )}
                      {!!dev.vendor && (
                        <span className="text-[10px] text-muted-foreground">
                          {String(dev.vendor)}
                        </span>
                      )}
                      {!!dev.serial && (
                        <span className="font-mono text-[10px] text-muted-foreground">
                          S/N: {String(dev.serial)}
                        </span>
                      )}
                      {!!dev.speed && (
                        <span className="text-[10px] text-muted-foreground">
                          {String(dev.speed)}
                        </span>
                      )}
                      {!!dev.battery && (
                        <span className="flex items-center gap-0.5 text-[10px] text-muted-foreground">
                          <Battery className="h-2.5 w-2.5" />
                          {String(dev.battery)}%
                        </span>
                      )}
                    </div>
                  </div>
                  {dev.connected !== undefined && (
                    <Badge
                      variant="outline"
                      className={`flex-shrink-0 text-[10px] px-1.5 py-0 ${
                        dev.connected
                          ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-500"
                          : "border-zinc-500/30 bg-zinc-500/10 text-zinc-400"
                      }`}
                    >
                      {dev.connected ? "Connected" : "Paired"}
                    </Badge>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      ))}

      {devices.length === 0 && !loading && (
        <p className="text-xs text-muted-foreground">
          No connected devices found.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Location Card
// ---------------------------------------------------------------------------

function LocationCard({ perm }: { perm: PermissionInfo | null }) {
  const [location, setLocation] = useState<Record<string, unknown> | null>(
    null,
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadLocation = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await engine.getLocation();
      const meta = result.metadata as Record<string, unknown> | null;
      setLocation(meta);
      if (result.type === "error") setError(result.output);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const lat = location?.latitude as number | null;
  const lon = location?.longitude as number | null;
  const accuracy = location?.accuracy_meters as number | null;
  const source = location?.source as string | null;

  const mapsUrl =
    lat !== null && lon !== null
      ? `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}&zoom=14`
      : null;

  return (
    <div className="space-y-4">
      <PermissionAlert perm={perm} />

      <Button
        variant="outline"
        size="sm"
        onClick={loadLocation}
        disabled={loading}
      >
        {loading ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <MapPin className="h-3.5 w-3.5" />
        )}
        Get Location
      </Button>

      {error && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-3 text-xs text-red-400">
          {error}
        </div>
      )}

      {location?.available === true && lat !== null && lon !== null ? (
        <div className="rounded-lg border bg-background p-4">
          <div className="flex items-start gap-3">
            <MapPin className="mt-0.5 h-5 w-5 flex-shrink-0 text-primary" />
            <div className="space-y-1">
              <p className="text-sm font-semibold">
                {lat.toFixed(6)}, {lon.toFixed(6)}
              </p>
              {accuracy !== null && (
                <p className="text-xs text-muted-foreground">
                  Accuracy: ±{Math.round(accuracy)}m
                </p>
              )}
              {source && (
                <p className="text-[10px] text-muted-foreground">
                  Source: {source}
                </p>
              )}
              {mapsUrl && (
                <a
                  href={mapsUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center gap-1 text-xs text-primary hover:underline"
                >
                  <ExternalLink className="h-3 w-3" />
                  Open in OpenStreetMap
                </a>
              )}
            </div>
          </div>
        </div>
      ) : (
        location !== null &&
        !loading && (
          <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3 text-xs text-amber-500">
            Location unavailable — {source ?? "permission may be required"}
          </div>
        )
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Accessibility Card
// ---------------------------------------------------------------------------

function AccessibilityCard({ perm }: { perm: PermissionInfo | null }) {
  if (!perm) return <p className="text-xs text-muted-foreground">Loading…</p>;
  return (
    <div className="space-y-3">
      {perm.status === "granted" ? (
        <div className="flex items-center gap-2 rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-3">
          <CheckCircle2 className="h-4 w-4 text-emerald-500" />
          <div>
            <p className="text-xs font-medium text-emerald-500">
              Accessibility access granted
            </p>
            <p className="text-[10px] text-muted-foreground mt-0.5">
              Window management, keyboard/mouse automation, and screen control
              are all available.
            </p>
          </div>
        </div>
      ) : (
        <PermissionAlert perm={perm} />
      )}
      <p className="text-xs text-muted-foreground">{perm.details}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// System Resources Card
// ---------------------------------------------------------------------------

function SystemResourcesCard({ engineStatus }: { engineStatus: EngineStatus }) {
  const [resources, setResources] = useState<Record<string, unknown> | null>(
    null,
  );
  const [loading, setLoading] = useState(false);
  // Pages are kept mounted (AppLayout keep-alive), so gate polling on the
  // route actually being visible — otherwise this polls forever in background.
  const isVisible = useLocation().pathname === "/devices";

  const load = useCallback(async () => {
    if (engineStatus !== "connected") return;
    setLoading(true);
    try {
      const result = await engine.getSystemResources();
      setResources(result.metadata);
    } catch {
      // non-critical
    } finally {
      setLoading(false);
    }
  }, [engineStatus]);

  useEffect(() => {
    if (!isVisible) return;
    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, [load, isVisible]);

  if (!resources) return null;

  const cpuPercent = Number(resources.cpu_percent ?? 0);
  const cpuCores = resources.cpu_cores as number | undefined;
  const cpuLogical = resources.cpu_logical as number | undefined;
  const cpuFreq = resources.cpu_freq as string | undefined;
  // Backend returns ram_* fields; support both names for forward compatibility
  const ramPercent = Number(
    resources.ram_percent ?? resources.memory_percent ?? 0,
  );
  const ramUsed = Number(
    resources.ram_used_gb ?? resources.memory_used_gb ?? 0,
  );
  const ramTotal = Number(
    resources.ram_total_gb ?? resources.memory_total_gb ?? 0,
  );
  const diskPercent = Number(resources.disk_percent ?? 0);
  const diskUsed = Number(resources.disk_used_gb ?? 0);
  const diskTotal = Number(resources.disk_total_gb ?? 0);

  // Format storage values: show TB when >= 1000 GB for readability
  const fmtStorage = (gb: number) =>
    gb >= 1000 ? `${(gb / 1024).toFixed(1)} TB` : `${gb.toFixed(0)} GB`;

  const cpuDetail = cpuCores
    ? `${cpuPercent.toFixed(0)}% · ${cpuCores}c/${cpuLogical ?? cpuCores}t${cpuFreq ? ` · ${cpuFreq}` : ""}`
    : `${cpuPercent.toFixed(0)}%`;

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <Cpu className="h-4 w-4 text-primary" />
          System Resources
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto h-6 text-xs"
            onClick={load}
            disabled={loading}
          >
            {loading ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <RefreshCw className="h-3 w-3" />
            )}
          </Button>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-3 gap-4">
          <ResourceBar label="CPU" percent={cpuPercent} detail={cpuDetail} />
          <ResourceBar
            label="RAM"
            percent={ramPercent}
            detail={`${ramUsed.toFixed(1)} / ${fmtStorage(ramTotal)}`}
          />
          <ResourceBar
            label="Disk"
            percent={diskPercent}
            detail={`${fmtStorage(diskUsed)} / ${fmtStorage(diskTotal)}`}
          />
        </div>
      </CardContent>
    </Card>
  );
}

function ResourceBar({
  label,
  percent,
  detail,
}: {
  label: string;
  percent: number;
  detail: string;
}) {
  const barColor =
    percent > 90
      ? "bg-red-500"
      : percent > 70
        ? "bg-amber-500"
        : "bg-emerald-500";
  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-xs font-medium">{label}</span>
        <span className="text-xs text-muted-foreground">{detail}</span>
      </div>
      <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${barColor}`}
          style={{ width: `${Math.min(100, percent)}%` }}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Devices page
// ---------------------------------------------------------------------------

export function Devices({ engineStatus }: DevicesProps) {
  const [loading, setLoading] = useState(false);
  const location = useLocation();
  const requestedPermission = useMemo(
    () => new URLSearchParams(location.search).get("permission"),
    [location.search],
  );

  // Shared stores only — the engine device list and the Tauri plugin Map both
  // live in PermissionsContext (ONE copy app-wide; the old private
  // page-level copy drifted from the Dashboard's).
  const {
    permissions: pluginPermissions,
    checkAll: recheckPlugin,
    isLoading: pluginLoading,
    devicePermissions,
    devicePlatform: platform,
    deviceLastRefresh: lastRefresh,
    refreshDevicePermissions,
  } = usePermissionsContext();

  const enginePermissions = useMemo(() => {
    const map: Record<string, PermissionInfo> = {};
    for (const ep of devicePermissions) {
      map[ep.permission] = ep;
    }
    return map;
  }, [devicePermissions]);

  const loadPermissions = useCallback(
    async (forceRefresh = false) => {
      if (engineStatus !== "connected") return;
      setLoading(true);
      try {
        await refreshDevicePermissions(forceRefresh);
      } catch (err) {
        logWarn("devices", "load permissions", err);
      } finally {
        setLoading(false);
      }
    },
    [engineStatus, refreshDevicePermissions],
  );

  useEffect(() => {
    loadPermissions();
  }, [loadPermissions]);

  /**
   * Build the merged permission map:
   * - For plugin-managed keys (mic, camera, screen_recording, accessibility,
   *   full_disk_access, input_monitoring), override the status field with the
   *   Tauri plugin's authoritative TCC value.
   * - For all other keys (bluetooth, wifi, network, location), use engine status.
   * - Keep the engine's device listing and instruction text in all cases.
   */
  const permissions = useMemo((): Record<string, PermissionInfo> => {
    const merged: Record<string, PermissionInfo> = { ...enginePermissions };
    for (const [key, pluginState] of pluginPermissions.entries()) {
      if (PLUGIN_KEYS.has(key)) {
        const engineEntry = enginePermissions[key];
        if (engineEntry) {
          merged[key] = {
            ...engineEntry,
            status: pluginState.status as PermissionStatusValue,
          };
        } else {
          merged[key] = {
            permission: key,
            status: pluginState.status as PermissionStatusValue,
            details: pluginState.detail ?? "",
            user_details: pluginState.description,
            user_instructions:
              pluginState.status !== "granted"
                ? `Open ${pluginState.label} settings to grant access.`
                : "",
            grant_instructions: pluginState.settingsUrl
              ? `Open: ${pluginState.settingsUrl}`
              : "",
            deep_link: pluginState.settingsUrl,
          } as PermissionInfo;
        }
      }
    }
    return merged;
  }, [enginePermissions, pluginPermissions]);

  const p = (key: string): PermissionInfo | null => permissions[key] ?? null;

  const grantedCount = Object.values(permissions).filter(
    (x) => x.status === "granted",
  ).length;
  const totalCount = Object.keys(permissions).length;
  const dedicatedPermissionKeys = new Set([
    "microphone",
    "camera",
    "screen_recording",
    "bluetooth",
    "wifi",
    "network",
    "location",
    "accessibility",
  ]);
  const additionalPermissions = Object.values(permissions)
    .filter((item) => !dedicatedPermissionKeys.has(item.permission))
    .sort((a, b) => a.permission.localeCompare(b.permission));

  const sections = [
    {
      key: "microphone",
      status: (p("microphone")?.status ?? "unknown") as PermissionStatusValue,
    },
    {
      key: "speakers",
      status: (p("microphone")?.status ?? "unknown") as PermissionStatusValue,
    },
    {
      key: "camera",
      status: (p("camera")?.status ?? "unknown") as PermissionStatusValue,
    },
    {
      key: "screen_recording",
      status: (p("screen_recording")?.status ??
        "unknown") as PermissionStatusValue,
    },
    {
      key: "bluetooth",
      status: (p("bluetooth")?.status ?? "unknown") as PermissionStatusValue,
    },
    {
      key: "wifi",
      status: (p("wifi")?.status ?? "unknown") as PermissionStatusValue,
    },
    {
      key: "network",
      status: (p("network")?.status ?? "unknown") as PermissionStatusValue,
    },
    { key: "connected", status: "granted" as PermissionStatusValue },
    {
      key: "location",
      status: (p("location")?.status ?? "unknown") as PermissionStatusValue,
    },
    {
      key: "accessibility",
      status: (p("accessibility")?.status ??
        "unknown") as PermissionStatusValue,
    },
  ];

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PageHeader
        title="Devices & Permissions"
        description={
          platform
            ? `${platform} — ${grantedCount}/${totalCount} permissions granted`
            : "Device access and system permissions"
        }
      >
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            loadPermissions(true);
            recheckPlugin();
          }}
          disabled={loading || pluginLoading || engineStatus !== "connected"}
        >
          {loading || pluginLoading ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <RefreshCw className="h-4 w-4" />
          )}
          Refresh All
        </Button>
      </PageHeader>

      <ScrollArea className="flex-1">
        <div className="mx-auto max-w-4xl space-y-4 p-6">
          {engineStatus !== "connected" ? (
            <Card>
              <CardContent className="py-12 text-center">
                <Monitor className="mx-auto mb-3 h-10 w-10 text-muted-foreground/50" />
                <p className="text-sm text-muted-foreground">
                  Connect to the engine to check device permissions and
                  capabilities
                </p>
              </CardContent>
            </Card>
          ) : (
            <>
              {/* Summary badges */}
              {totalCount > 0 && (
                <div className="flex flex-wrap gap-2">
                  {sections.map(({ key, status }) => {
                    const cfg = STATUS_CONFIG[status] ?? STATUS_CONFIG.unknown;
                    return (
                      <Badge
                        key={key}
                        variant="outline"
                        className={`gap-1.5 ${cfg.bgColor} ${cfg.color} border`}
                      >
                        <span className={cfg.color}>{cfg.icon}</span>
                        {key.replace("_", " ")}
                      </Badge>
                    );
                  })}
                  {lastRefresh && (
                    <span className="ml-auto self-center text-xs text-muted-foreground">
                      Last checked:{" "}
                      {lastRefresh.toLocaleTimeString([], {
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </span>
                  )}
                </div>
              )}

              {/* Microphone */}
              <SectionCard
                icon={<Mic className="h-5 w-5" />}
                title="Microphone"
                status={
                  (p("microphone")?.status ??
                    "unknown") as PermissionStatusValue
                }
                description={
                  p("microphone")?.user_details ||
                  "Audio input for voice commands, recording, and transcription"
                }
                defaultOpen={
                  requestedPermission === null || requestedPermission === "microphone"
                }
              >
                <MicrophoneCard perm={p("microphone")} />
              </SectionCard>

              {/* Speakers */}
              <SectionCard
                icon={<Volume2 className="h-5 w-5" />}
                title="Speakers / Audio Output"
                status="granted"
                description="Audio output devices for playback"
              >
                <SpeakersCard perm={p("microphone")} />
              </SectionCard>

              {/* Camera */}
              <SectionCard
                icon={<Camera className="h-5 w-5" />}
                title="Camera"
                status={
                  (p("camera")?.status ?? "unknown") as PermissionStatusValue
                }
                description={
                  p("camera")?.user_details ||
                  "Video input for capture and video calls"
                }
                defaultOpen={requestedPermission === "camera"}
              >
                <CameraCard perm={p("camera")} />
              </SectionCard>

              {/* Screen Recording */}
              <SectionCard
                icon={<Eye className="h-5 w-5" />}
                title="Screen Recording"
                status={
                  (p("screen_recording")?.status ??
                    "unknown") as PermissionStatusValue
                }
                description={
                  p("screen_recording")?.user_details ||
                  "Screen capture for screenshots and video recording"
                }
                defaultOpen={requestedPermission === "screen_recording"}
              >
                <ScreenRecordingCard perm={p("screen_recording")} />
              </SectionCard>

              {/* Bluetooth */}
              <SectionCard
                icon={<Bluetooth className="h-5 w-5" />}
                title="Bluetooth"
                status={
                  (p("bluetooth")?.status ?? "unknown") as PermissionStatusValue
                }
                description={
                  p("bluetooth")?.user_details ||
                  "Bluetooth peripherals and smart devices"
                }
                defaultOpen={requestedPermission === "bluetooth"}
              >
                <BluetoothCard perm={p("bluetooth")} />
              </SectionCard>

              {/* WiFi */}
              <SectionCard
                icon={<Wifi className="h-5 w-5" />}
                title="WiFi Networks"
                status={
                  (p("wifi")?.status ?? "unknown") as PermissionStatusValue
                }
                description={
                  p("wifi")?.user_details ||
                  "Scan and discover WiFi networks in range"
                }
                defaultOpen={requestedPermission === "wifi"}
              >
                <WifiCard perm={p("wifi")} />
              </SectionCard>

              {/* Network Interfaces */}
              <SectionCard
                icon={<Network className="h-5 w-5" />}
                title="Network Interfaces"
                status={
                  (p("network")?.status ?? "unknown") as PermissionStatusValue
                }
                description={
                  p("network")?.user_details ||
                  "Network adapters, IP addresses, and connectivity status"
                }
                defaultOpen={requestedPermission === "network"}
              >
                <NetworkCard perm={p("network")} />
              </SectionCard>

              {/* Connected Devices */}
              <SectionCard
                icon={<Usb className="h-5 w-5" />}
                title="Connected Devices"
                status="granted"
                description="Monitors, USB devices, peripherals, and connected hardware"
              >
                <ConnectedDevicesCard />
              </SectionCard>

              {/* Location */}
              <SectionCard
                icon={<MapPin className="h-5 w-5" />}
                title="Location"
                status={
                  (p("location")?.status ?? "unknown") as PermissionStatusValue
                }
                description={
                  p("location")?.user_details ||
                  "Device location via GPS or network"
                }
                defaultOpen={requestedPermission === "location"}
              >
                <LocationCard perm={p("location")} />
              </SectionCard>

              {/* Accessibility */}
              <SectionCard
                icon={<Shield className="h-5 w-5" />}
                title="Accessibility"
                status={
                  (p("accessibility")?.status ??
                    "unknown") as PermissionStatusValue
                }
                description={
                  p("accessibility")?.user_details ||
                  "Keyboard/mouse automation, window management, and screen control"
                }
                defaultOpen={requestedPermission === "accessibility"}
              >
                <AccessibilityCard perm={p("accessibility")} />
              </SectionCard>

              {additionalPermissions.map((permission) => (
                <SectionCard
                  key={permission.permission}
                  icon={<Shield className="h-5 w-5" />}
                  title={permission.permission
                    .replace(/_/g, " ")
                    .replace(/\b\w/g, (character) => character.toUpperCase())}
                  status={permission.status}
                  description={
                    permission.user_details ||
                    permission.details ||
                    "Optional operating-system access"
                  }
                  defaultOpen={requestedPermission === permission.permission}
                >
                  <PermissionAlert perm={permission} />
                </SectionCard>
              ))}

              {/* System Resources */}
              <SystemResourcesCard engineStatus={engineStatus} />
            </>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
