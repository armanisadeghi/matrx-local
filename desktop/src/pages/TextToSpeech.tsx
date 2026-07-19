import { useState, useEffect, useRef, useCallback } from "react";
import { PageHeader } from "@/components/layout/PageHeader";
import { SubTabBar } from "@/components/layout/SubTabBar";
import { useTtsApp } from "@/contexts/TtsContext";
import { useTts } from "@/hooks/use-tts";
import type { TtsVoice } from "@/lib/tts/types";
import type { TtsHistoryEntry, TtsPlaybackState } from "@/hooks/use-tts";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { cn } from "@/lib/utils";
import { parseMarkdownToText } from "@/lib/parse-markdown-for-speech";
import {
  AudioLines,
  Play,
  Pause,
  Square,
  Download,
  Loader2,
  CheckCircle2,
  AlertCircle,
  Trash2,
  Clock,
  Zap,
  Star,
  StarOff,
  Search,
  X,
  ChevronDown,
  HardDrive,
  RefreshCw,
  FileText,
  Blend,
  Plus,
  Save,
  Pencil,
  Upload,
  Sliders,
  ExternalLink,
} from "lucide-react";
import { loadSettings, saveSetting } from "@/lib/settings";
import {
  blendPreview,
  saveBlendedVoice,
  listCustomVoices,
  renameCustomVoice,
  deleteCustomVoice,
  importVoiceFile,
  type BlendComponent,
  type CustomVoiceInfo,
} from "@/lib/tts/api";

const TABS = [
  { value: "speak", label: "Speak" },
  { value: "voices", label: "Voices" },
  { value: "blend", label: "Blend" },
  { value: "clone", label: "Clone Voice (Soon)" },
  { value: "import", label: "Import" },
  { value: "settings", label: "Settings" },
];

export function TextToSpeech() {
  const [tab, setTab] = useState("speak");
  const [state, actions] = useTtsApp();

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Text to Speech"
        description="Kokoro TTS — local AI voice synthesis"
      >
        <StatusBadge status={state.status} />
      </PageHeader>
      <SubTabBar tabs={TABS} value={tab} onValueChange={setTab} />
      <div className="flex-1 overflow-y-auto">
        {tab === "speak" && <SpeakTab state={state} actions={actions} />}
        {tab === "voices" && <VoicesTab state={state} actions={actions} />}
        {tab === "blend" && <BlendTab state={state} actions={actions} />}
        {tab === "clone" && <CloneVoiceTab />}
        {tab === "import" && (
          <ImportVoicesTab state={state} actions={actions} />
        )}
        {tab === "settings" && <SettingsTab state={state} actions={actions} />}
      </div>
    </div>
  );
}

function StatusBadge({
  status,
}: {
  status: ReturnType<typeof useTts>[0]["status"];
}) {
  if (!status) return null;
  if (!status.model_downloaded) {
    return (
      <span className="flex items-center gap-1.5 rounded-full bg-muted px-3 py-1 text-xs font-medium text-muted-foreground">
        <Download className="h-3 w-3" />
        Model Required
      </span>
    );
  }
  if (status.model_loaded) {
    return (
      <span className="flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-3 py-1 text-xs font-medium text-emerald-500">
        <CheckCircle2 className="h-3 w-3" />
        Ready
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1.5 rounded-full bg-blue-500/10 px-3 py-1 text-xs font-medium text-blue-700 dark:text-blue-400">
      <HardDrive className="h-3 w-3" />
      Downloaded
    </span>
  );
}

// ── Speak Tab ────────────────────────────────────────────────────────────────

function SpeakTab({
  state,
  actions,
}: {
  state: ReturnType<typeof useTts>[0];
  actions: ReturnType<typeof useTts>[1];
}) {
  const [text, setText] = useState("");
  const textRef = useRef<HTMLTextAreaElement>(null);
  const [autoClean, setAutoClean] = useState(false);

  useEffect(() => {
    loadSettings().then((s) => {
      setAutoClean(s.ttsAutoCleanMarkdown);
    });
  }, []);

  const ps = state.playbackState;
  const isActive = ps === "synthesizing" || ps === "playing" || ps === "paused";
  const isSynthesizing = ps === "synthesizing";
  const isPlaying = ps === "playing";
  const isPaused = ps === "paused";

  const needsDownload = state.status && !state.status.model_downloaded;
  const isReady = state.status?.model_downloaded ?? false;
  const canSpeak = isReady && text.trim().length > 0 && !isActive;

  const handleSpeak = useCallback(() => {
    if (!canSpeak) return;
    const spokenText = autoClean ? parseMarkdownToText(text) : text;
    actions.speakStreaming(spokenText);
  }, [canSpeak, text, actions, autoClean]);

  const handleStop = useCallback(() => actions.stopAudio(), [actions]);
  const handlePause = useCallback(() => actions.pauseAudio(), [actions]);
  const handleResume = useCallback(() => actions.resumeAudio(), [actions]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        if (!isActive) handleSpeak();
      }
      if (e.key === " " && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        if (isPlaying) handlePause();
        else if (isPaused) handleResume();
      }
      if (e.key === "Escape" && isActive) {
        e.preventDefault();
        handleStop();
      }
    },
    [
      handleSpeak,
      handleStop,
      handlePause,
      handleResume,
      isActive,
      isPlaying,
      isPaused,
    ],
  );

  const handleCleanMarkdown = useCallback(() => {
    if (!text.trim()) return;
    setText(parseMarkdownToText(text));
  }, [text]);

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-6 p-6">
      {needsDownload && (
        <DownloadBanner
          status={state.status!}
          isDownloading={state.isDownloading}
          onDownload={actions.downloadModel}
        />
      )}

      {state.error && (
        <div className="flex items-start gap-3 rounded-lg border border-red-500/20 bg-red-500/5 p-4">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-500" />
          <div className="flex-1">
            <p className="text-sm text-red-700 dark:text-red-400">{state.error}</p>
          </div>
          <button
            onClick={actions.clearError}
            className="text-red-700 dark:text-red-400 hover:text-red-300"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {/* Text input */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <label className="text-sm font-medium text-foreground">Text</label>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              className="h-6 gap-1 px-2 text-xs text-muted-foreground"
              onClick={handleCleanMarkdown}
              disabled={!text.trim()}
              title="Clean markdown formatting for natural speech"
            >
              <FileText className="h-3 w-3" />
              Clean Markdown
            </Button>
            <span className="text-xs text-muted-foreground">
              {text.length.toLocaleString()} characters
            </span>
          </div>
        </div>
        <div className="relative">
          <textarea
            ref={textRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Enter text to speak..."
            className="min-h-[160px] w-full resize-y rounded-lg border bg-background p-4 text-sm leading-relaxed focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
            disabled={!isReady}
          />
          {isActive && (
            <SynthesizingOverlay
              playbackState={ps}
              elapsed={state.currentElapsed}
              onStop={handleStop}
              onPause={handlePause}
              onResume={handleResume}
            />
          )}
        </div>
      </div>

      {/* Controls row */}
      <div className="flex flex-wrap items-end gap-4">
        {/* Voice selector */}
        <div className="min-w-[200px] flex-1 space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">
            Voice
          </label>
          <VoiceSelector
            voices={state.voices}
            selected={state.selectedVoice}
            onChange={actions.setSelectedVoice}
            disabled={!isReady || isActive}
          />
        </div>

        {/* Speed slider */}
        <div className="w-48 space-y-1.5">
          <div className="flex items-center justify-between">
            <label className="text-xs font-medium text-muted-foreground">
              Speed
            </label>
            <span className="text-xs tabular-nums text-muted-foreground">
              {state.speed.toFixed(1)}x
            </span>
          </div>
          <Slider
            min={0.5}
            max={2}
            step={0.05}
            value={[state.speed]}
            onValueChange={([v]) => {
              if (v !== undefined) actions.setSpeed(v);
            }}
            disabled={!isReady || isActive}
          />
        </div>

        {/* Transport controls */}
        <div className="flex gap-2">
          {!isActive && (
            <Button
              onClick={handleSpeak}
              disabled={!canSpeak}
              size="lg"
              className="gap-2"
            >
              <AudioLines className="h-4 w-4" />
              Speak
            </Button>
          )}

          {isSynthesizing && (
            <Button
              size="lg"
              variant="outline"
              disabled
              className="gap-2 opacity-60"
            >
              <Loader2 className="h-4 w-4 animate-spin" />
              Synthesizing…
            </Button>
          )}

          {(isPlaying || isPaused) && (
            <Button
              onClick={isPaused ? handleResume : handlePause}
              size="lg"
              variant="outline"
              className="gap-2"
            >
              {isPaused ? (
                <>
                  <Play className="h-4 w-4" />
                  Resume
                </>
              ) : (
                <>
                  <Pause className="h-4 w-4" />
                  Pause
                </>
              )}
            </Button>
          )}

          {isActive && (
            <Button
              onClick={handleStop}
              size="lg"
              variant="outline"
              className="gap-2 border-destructive/40 text-destructive hover:bg-destructive/10"
            >
              <Square className="h-4 w-4 fill-current" />
              Stop
            </Button>
          )}
        </div>
      </div>

      {/* Elapsed timing after stream completes */}
      {ps === "idle" && state.currentElapsed > 0 && (
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          {state.currentDuration > 0 && (
            <span className="flex items-center gap-1">
              <Clock className="h-3 w-3" />
              {state.currentDuration.toFixed(1)}s audio
            </span>
          )}
          <span className="flex items-center gap-1">
            <Zap className="h-3 w-3" />
            {state.currentElapsed.toFixed(2)}s total
          </span>
        </div>
      )}

      {/* History */}
      {state.history.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium text-muted-foreground">
              Recent
            </h3>
            <Button
              variant="ghost"
              size="sm"
              onClick={actions.clearHistory}
              className="h-7 text-xs"
            >
              <Trash2 className="mr-1 h-3 w-3" />
              Clear
            </Button>
          </div>
          <div className="space-y-2">
            {state.history.slice(0, 10).map((entry) => (
              <HistoryItem key={entry.id} entry={entry} />
            ))}
          </div>
        </div>
      )}

      {/* Keyboard shortcut hint */}
      {isReady && (
        <p className="text-center text-xs text-muted-foreground/60">
          <kbd className="rounded border px-1.5 py-0.5 text-[10px] font-mono">
            ⌘↵
          </kbd>{" "}
          speak
          {" · "}
          <kbd className="rounded border px-1.5 py-0.5 text-[10px] font-mono">
            ⌘Space
          </kbd>{" "}
          pause/resume
          {" · "}
          <kbd className="rounded border px-1.5 py-0.5 text-[10px] font-mono">
            Esc
          </kbd>{" "}
          stop
        </p>
      )}
    </div>
  );
}

function SynthesizingOverlay({
  playbackState,
  elapsed,
  onStop,
  onPause,
  onResume,
}: {
  playbackState: TtsPlaybackState;
  elapsed: number;
  onStop: () => void;
  onPause: () => void;
  onResume: () => void;
}) {
  const isPaused = playbackState === "paused";
  const isSynth = playbackState === "synthesizing";

  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-end justify-between px-3 pb-2.5">
      {/* Left: waveform + state label */}
      <div className="flex items-center gap-2">
        <WaveformBars paused={isPaused} />
        <span className="text-[11px] font-medium text-primary/80 tabular-nums">
          {isSynth
            ? "Synthesizing…"
            : isPaused
              ? "Paused"
              : elapsed > 0
                ? `${elapsed.toFixed(1)}s`
                : "Playing…"}
        </span>
      </div>
      {/* Right: pause/resume + stop */}
      <div className="pointer-events-auto flex items-center gap-1">
        {!isSynth && (
          <button
            onClick={isPaused ? onResume : onPause}
            className="flex h-6 items-center gap-1 rounded-full border border-primary/30 bg-background/80 px-2.5 text-[11px] font-medium text-primary backdrop-blur-sm transition-colors hover:bg-primary/10"
          >
            {isPaused ? (
              <>
                <Play className="h-2.5 w-2.5" /> Resume
              </>
            ) : (
              <>
                <Pause className="h-2.5 w-2.5" /> Pause
              </>
            )}
          </button>
        )}
        <button
          onClick={onStop}
          className="flex h-6 items-center gap-1 rounded-full border border-destructive/30 bg-background/80 px-2.5 text-[11px] font-medium text-destructive backdrop-blur-sm transition-colors hover:bg-destructive/10"
        >
          <Square className="h-2.5 w-2.5 fill-current" />
          Stop
        </button>
      </div>
    </div>
  );
}

function WaveformBars({ paused }: { paused?: boolean }) {
  return (
    <span className="flex items-end gap-[2px]" aria-label="audio playback">
      {[0, 1, 2, 3, 4].map((i) => (
        <span
          key={i}
          className="w-[3px] rounded-full bg-primary/70"
          style={{
            height: "14px",
            animation: paused
              ? "none"
              : `tts-bar 0.9s ease-in-out ${i * 0.12}s infinite alternate`,
            transform: paused ? "scaleY(0.25)" : undefined,
            opacity: paused ? 0.35 : undefined,
          }}
        />
      ))}
      <style>{`
        @keyframes tts-bar {
          from { transform: scaleY(0.2); opacity: 0.4; }
          to   { transform: scaleY(1);   opacity: 1;   }
        }
      `}</style>
    </span>
  );
}

function HistoryItem({ entry }: { entry: TtsHistoryEntry }) {
  const [isPlaying, setIsPlaying] = useState(false);
  const [playError, setPlayError] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  // Reset transient state if the entry's URL changes (e.g. parent re-renders
  // a different entry into this slot — keys should prevent this, but be safe).
  useEffect(() => {
    return () => {
      if (audioRef.current) {
        audioRef.current.onended = null;
        audioRef.current.onerror = null;
        audioRef.current.pause();
        audioRef.current = null;
      }
    };
  }, []);

  const hasAudio = Boolean(entry.audioUrl);

  const handleClick = useCallback(() => {
    if (!hasAudio) return;
    setPlayError(null);

    if (isPlaying && audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
      setIsPlaying(false);
      return;
    }

    const audio = new Audio(entry.audioUrl);
    audioRef.current = audio;
    audio.onended = () => {
      setIsPlaying(false);
      audioRef.current = null;
    };
    audio.onerror = () => {
      setIsPlaying(false);
      setPlayError("Audio failed to load");
      audioRef.current = null;
    };
    audio
      .play()
      .then(() => setIsPlaying(true))
      .catch((e) => {
        setIsPlaying(false);
        setPlayError(e instanceof Error ? e.message : "Playback was blocked");
        audioRef.current = null;
      });
  }, [hasAudio, isPlaying, entry.audioUrl]);

  return (
    <div className="flex items-center gap-3 rounded-lg border bg-card/50 px-4 py-2.5">
      <button
        onClick={handleClick}
        disabled={!hasAudio}
        title={
          !hasAudio
            ? "Audio is no longer available"
            : isPlaying
              ? "Stop"
              : "Play"
        }
        className={cn(
          "flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition-colors",
          hasAudio
            ? isPlaying
              ? "bg-primary text-primary-foreground hover:bg-primary/90"
              : "bg-primary/10 text-primary hover:bg-primary/20"
            : "cursor-not-allowed bg-muted text-muted-foreground/50",
        )}
      >
        {isPlaying ? (
          <Square className="h-3 w-3 fill-current" />
        ) : (
          <Play className="h-3.5 w-3.5" />
        )}
      </button>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm">{entry.text}</p>
        <p className="text-xs text-muted-foreground">
          {entry.voiceName}
          {entry.duration > 0 && (
            <> &middot; {entry.duration.toFixed(1)}s</>
          )}{" "}
          &middot; {new Date(entry.createdAt).toLocaleTimeString()}
          {playError && (
            <span className="ml-2 text-red-700 dark:text-red-400">· {playError}</span>
          )}
        </p>
      </div>
    </div>
  );
}

// ── Voice Selector ───────────────────────────────────────────────────────────

function VoiceSelector({
  voices,
  selected,
  onChange,
  disabled,
}: {
  voices: TtsVoice[];
  selected: string;
  onChange: (id: string) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const selectedVoice = voices.find((v) => v.voice_id === selected);

  const grouped = new Map<string, TtsVoice[]>();
  for (const v of voices) {
    if (!grouped.has(v.language)) grouped.set(v.language, []);
    grouped.get(v.language)!.push(v);
  }

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => !disabled && setOpen(!open)}
        disabled={disabled}
        className={cn(
          "flex h-9 w-full items-center justify-between rounded-md border bg-background px-3 text-sm",
          "hover:bg-accent/50 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary",
          disabled && "cursor-not-allowed opacity-50",
        )}
      >
        <span className="truncate">
          {selectedVoice
            ? `${selectedVoice.name} (${selectedVoice.language})`
            : "Select a voice..."}
        </span>
        <ChevronDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
      </button>

      {open && (
        <div className="absolute left-0 top-full z-50 mt-1 max-h-72 w-full overflow-y-auto rounded-lg border bg-popover shadow-lg">
          {Array.from(grouped).map(([lang, langVoices]) => (
            <div key={lang}>
              <div className="sticky top-0 bg-popover px-3 py-1.5 text-xs font-semibold text-muted-foreground">
                {lang}
              </div>
              {langVoices.map((v) => (
                <button
                  key={v.voice_id}
                  onClick={() => {
                    onChange(v.voice_id);
                    setOpen(false);
                  }}
                  className={cn(
                    "flex w-full items-center gap-2 px-3 py-1.5 text-sm hover:bg-accent",
                    v.voice_id === selected && "bg-accent",
                  )}
                >
                  <span
                    className={cn(
                      "h-1.5 w-1.5 rounded-full shrink-0",
                      v.gender === "female" ? "bg-pink-400" : "bg-blue-400",
                    )}
                  />
                  <span className="flex-1 text-left">{v.name}</span>
                  <span className="text-xs text-muted-foreground">
                    {v.quality_grade}
                  </span>
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Voices Tab ───────────────────────────────────────────────────────────────

function VoicesTab({
  state,
  actions,
}: {
  state: ReturnType<typeof useTts>[0];
  actions: ReturnType<typeof useTts>[1];
}) {
  const [search, setSearch] = useState("");
  const [filterLang, setFilterLang] = useState<string | null>(null);
  const [filterGender, setFilterGender] = useState<string | null>(null);
  const [favorites, setFavorites] = useState<string[]>([]);

  useEffect(() => {
    loadSettings().then((s) => setFavorites(s.ttsFavoriteVoices ?? []));
  }, []);

  const toggleFavorite = useCallback((voiceId: string) => {
    setFavorites((prev) => {
      const next = prev.includes(voiceId)
        ? prev.filter((id) => id !== voiceId)
        : [...prev, voiceId];
      saveSetting("ttsFavoriteVoices", next);
      return next;
    });
  }, []);

  const filtered = state.voices.filter((v) => {
    if (
      search &&
      !v.name.toLowerCase().includes(search.toLowerCase()) &&
      !v.voice_id.includes(search.toLowerCase())
    ) {
      return false;
    }
    if (filterLang && v.language !== filterLang) return false;
    if (filterGender && v.gender !== filterGender) return false;
    return true;
  });

  const favVoices = filtered.filter((v) => favorites.includes(v.voice_id));
  const otherVoices = filtered.filter((v) => !favorites.includes(v.voice_id));

  const languages = [...new Set(state.voices.map((v) => v.language))];

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6 p-6">
      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search voices..."
            className="h-9 w-full rounded-md border bg-background pl-9 pr-3 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
          />
          {search && (
            <button
              onClick={() => setSearch("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
        <select
          value={filterLang ?? ""}
          onChange={(e) => setFilterLang(e.target.value || null)}
          className="h-9 rounded-md border bg-background px-3 text-sm"
        >
          <option value="">All Languages</option>
          {languages.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
        <select
          value={filterGender ?? ""}
          onChange={(e) => setFilterGender(e.target.value || null)}
          className="h-9 rounded-md border bg-background px-3 text-sm"
        >
          <option value="">All Genders</option>
          <option value="female">Female</option>
          <option value="male">Male</option>
        </select>
        <span className="text-xs text-muted-foreground">
          {filtered.length} of {state.voices.length} voices
        </span>
      </div>

      {/* Favorites */}
      {favVoices.length > 0 && (
        <div className="space-y-3">
          <h3 className="flex items-center gap-2 text-sm font-medium text-amber-700 dark:text-amber-400">
            <Star className="h-4 w-4 fill-amber-400" />
            Favorites
          </h3>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {favVoices.map((v) => (
              <VoiceCard
                key={v.voice_id}
                voice={v}
                isFavorite
                isSelected={v.voice_id === state.selectedVoice}
                isPlaying={state.isPreviewPlaying === v.voice_id}
                onSelect={() => actions.setSelectedVoice(v.voice_id)}
                onPreview={() => actions.preview(v.voice_id)}
                onToggleFavorite={() => toggleFavorite(v.voice_id)}
                modelReady={state.status?.model_downloaded ?? false}
              />
            ))}
          </div>
        </div>
      )}

      {/* All voices by language */}
      {state.languageGroups.map((group) => {
        const groupVoices = otherVoices.filter(
          (v) => v.language === group.language,
        );
        if (groupVoices.length === 0) return null;
        return (
          <div key={group.language} className="space-y-3">
            <h3 className="text-sm font-medium text-muted-foreground">
              {group.language}
              <span className="ml-2 text-xs font-normal">
                ({groupVoices.length})
              </span>
            </h3>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {groupVoices.map((v) => (
                <VoiceCard
                  key={v.voice_id}
                  voice={v}
                  isFavorite={false}
                  isSelected={v.voice_id === state.selectedVoice}
                  isPlaying={state.isPreviewPlaying === v.voice_id}
                  onSelect={() => actions.setSelectedVoice(v.voice_id)}
                  onPreview={() => actions.preview(v.voice_id)}
                  onToggleFavorite={() => toggleFavorite(v.voice_id)}
                  modelReady={state.status?.model_downloaded ?? false}
                />
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function VoiceCard({
  voice,
  isFavorite,
  isSelected,
  isPlaying,
  onSelect,
  onPreview,
  onToggleFavorite,
  modelReady,
}: {
  voice: TtsVoice;
  isFavorite: boolean;
  isSelected: boolean;
  isPlaying: boolean;
  onSelect: () => void;
  onPreview: () => void;
  onToggleFavorite: () => void;
  modelReady: boolean;
}) {
  return (
    <div
      className={cn(
        "group relative flex items-center gap-3 rounded-lg border p-3 transition-colors",
        isSelected
          ? "border-primary bg-primary/5"
          : "border-border hover:border-primary/30 hover:bg-accent/50",
      )}
    >
      {/* Preview button */}
      <button
        onClick={onPreview}
        disabled={!modelReady}
        className={cn(
          "flex h-10 w-10 shrink-0 items-center justify-center rounded-full transition-colors",
          isPlaying
            ? "bg-primary text-primary-foreground"
            : "bg-muted text-muted-foreground hover:bg-primary/20 hover:text-primary",
          !modelReady && "cursor-not-allowed opacity-40",
        )}
      >
        {isPlaying ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Play className="h-4 w-4" />
        )}
      </button>

      {/* Info */}
      <div className="min-w-0 flex-1 cursor-pointer" onClick={onSelect}>
        <div className="flex items-center gap-2">
          <span
            className={cn(
              "h-2 w-2 rounded-full shrink-0",
              voice.gender === "female" ? "bg-pink-400" : "bg-blue-400",
            )}
          />
          <span className="truncate text-sm font-medium">{voice.name}</span>
          {voice.is_default && (
            <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
              Default
            </span>
          )}
          {voice.is_custom && (
            <span className="rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-500">
              Custom
            </span>
          )}
        </div>
        <p className="mt-0.5 text-xs text-muted-foreground">
          <span className="capitalize">{voice.gender}</span> &middot; Grade{" "}
          {voice.quality_grade}
          {voice.voice_id !== voice.name && (
            <span className="ml-1 font-mono opacity-60">{voice.voice_id}</span>
          )}
        </p>
      </div>

      {/* Favorite toggle */}
      <button
        onClick={onToggleFavorite}
        className="shrink-0 text-muted-foreground hover:text-amber-400"
      >
        {isFavorite ? (
          <Star className="h-4 w-4 fill-amber-400 text-amber-700 dark:text-amber-400" />
        ) : (
          <StarOff className="h-4 w-4 opacity-0 group-hover:opacity-100" />
        )}
      </button>
    </div>
  );
}

// ── Blend Tab ─────────────────────────────────────────────────────────────────

const BUILTIN_VOICE_IDS_FOR_BLEND = [
  "af_heart",
  "af_bella",
  "af_nicole",
  "af_sky",
  "af_aoede",
  "af_kore",
  "af_sarah",
  "af_jessica",
  "af_nova",
  "af_river",
  "af_alloy",
  "am_adam",
  "am_echo",
  "am_eric",
  "am_fenrir",
  "am_liam",
  "am_michael",
  "am_onyx",
  "am_puck",
  "bf_alice",
  "bf_emma",
  "bf_isabella",
  "bf_lily",
  "bm_daniel",
  "bm_fable",
  "bm_george",
  "bm_lewis",
  "ef_dora",
  "em_alex",
  "ff_siwis",
  "hf_alpha",
  "hf_beta",
  "hm_omega",
  "hm_psi",
  "if_sara",
  "im_nicola",
  "jf_alpha",
  "jf_gongitsune",
  "jm_kumo",
  "pf_dora",
  "pm_alex",
  "zf_xiaobei",
  "zf_xiaoni",
  "zf_xiaoxiao",
  "zf_xiaoyi",
  "zm_yunjian",
  "zm_yunxi",
  "zm_yunxia",
  "zm_yunyang",
];

function BlendTab({
  state,
  actions,
}: {
  state: ReturnType<typeof useTts>[0];
  actions: ReturnType<typeof useTts>[1];
}) {
  const isReady = state.status?.model_downloaded ?? false;

  const [components, setComponents] = useState<BlendComponent[]>([
    { voice_id: "af_heart", weight: 0.5 },
    { voice_id: "af_bella", weight: 0.5 },
  ]);
  const [isPreviewing, setIsPreviewing] = useState(false);
  const [isPlayingPreview, setIsPlayingPreview] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [saveName, setSaveName] = useState("");
  const [saveId, setSaveId] = useState("");
  const [saveGender, setSaveGender] = useState("female");
  const [saveLangCode, setSaveLangCode] = useState("a");
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const addComponent = useCallback(() => {
    setComponents((prev) => [...prev, { voice_id: "af_heart", weight: 0.3 }]);
    setPreviewUrl(null);
  }, []);

  const removeComponent = useCallback((idx: number) => {
    setComponents((prev) => prev.filter((_, i) => i !== idx));
    setPreviewUrl(null);
  }, []);

  const updateComponent = useCallback(
    (idx: number, patch: Partial<BlendComponent>) => {
      setComponents((prev) =>
        prev.map((c, i) => (i === idx ? { ...c, ...patch } : c)),
      );
      setPreviewUrl(null);
    },
    [],
  );

  const totalWeight = components.reduce((s, c) => s + c.weight, 0);

  const handlePreview = useCallback(async () => {
    if (!isReady || components.length === 0) return;
    setIsPreviewing(true);
    setError(null);
    setSaveSuccess(false);
    try {
      const { blob } = await blendPreview(components);
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      const url = URL.createObjectURL(blob);
      setPreviewUrl(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setIsPreviewing(false);
    }
  }, [isReady, components, previewUrl]);

  const handleSave = useCallback(async () => {
    if (!saveName.trim() || !saveId.trim()) return;
    setIsSaving(true);
    setError(null);
    try {
      const result = await saveBlendedVoice({
        voice_id: saveId,
        name: saveName,
        components,
        gender: saveGender,
        lang_code: saveLangCode,
      });
      if (!result.success) throw new Error(result.error ?? "Save failed");
      setSaveSuccess(true);
      // Refresh voice list so it shows up in the selector
      await actions.refreshVoices();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setIsSaving(false);
    }
  }, [saveName, saveId, components, saveGender, saveLangCode, actions]);

  const idFromName = (n: string) =>
    n
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_|_$/g, "")
      .slice(0, 40);

  if (!isReady) {
    return (
      <div className="flex flex-col items-center justify-center gap-4 p-12 text-center">
        <HardDrive className="h-10 w-10 text-muted-foreground/40" />
        <p className="text-sm text-muted-foreground">
          Download the TTS model first to use voice blending.
        </p>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6 p-6">
      <div className="space-y-1">
        <h2 className="text-base font-semibold">Voice Blender</h2>
        <p className="text-xs text-muted-foreground">
          Mix any builtin voices at custom ratios to create new voice
          characters. Weights are automatically normalised.
        </p>
      </div>

      {error && (
        <div className="flex items-start gap-3 rounded-lg border border-red-500/20 bg-red-500/5 p-3">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-500" />
          <p className="flex-1 text-sm text-red-700 dark:text-red-400">{error}</p>
          <button
            onClick={() => setError(null)}
            className="text-red-700 dark:text-red-400 hover:text-red-300"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {saveSuccess && (
        <div className="flex items-center gap-2 rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-3">
          <CheckCircle2 className="h-4 w-4 text-emerald-500" />
          <p className="text-sm text-emerald-700 dark:text-emerald-400">
            Voice "<span className="font-medium">{saveName}</span>" saved! It's
            now available in the voice selector.
          </p>
        </div>
      )}

      {/* Component mixer */}
      <section className="space-y-3 rounded-lg border p-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium flex items-center gap-2">
            <Sliders className="h-4 w-4" />
            Blend Components
          </h3>
          <span className="text-xs text-muted-foreground">
            Total weight: {totalWeight.toFixed(2)}
          </span>
        </div>

        <div className="space-y-4">
          {components.map((comp, idx) => (
            <div key={idx} className="space-y-2">
              <div className="flex items-center gap-3">
                <select
                  value={comp.voice_id}
                  onChange={(e) =>
                    updateComponent(idx, { voice_id: e.target.value })
                  }
                  className="flex-1 h-8 rounded-md border bg-background px-2 text-sm"
                >
                  {BUILTIN_VOICE_IDS_FOR_BLEND.map((id) => (
                    <option key={id} value={id}>
                      {id}
                    </option>
                  ))}
                </select>
                <span className="w-12 text-right text-xs tabular-nums text-muted-foreground">
                  {(comp.weight * 100).toFixed(0)}%
                </span>
                {components.length > 2 && (
                  <button
                    onClick={() => removeComponent(idx)}
                    className="text-muted-foreground hover:text-destructive"
                  >
                    <X className="h-4 w-4" />
                  </button>
                )}
              </div>
              <Slider
                min={0}
                max={1}
                step={0.01}
                value={[comp.weight]}
                onValueChange={([v]) => {
                  if (v !== undefined) updateComponent(idx, { weight: v });
                }}
              />
            </div>
          ))}
        </div>

        {components.length < 8 && (
          <Button
            variant="outline"
            size="sm"
            className="gap-1.5"
            onClick={addComponent}
          >
            <Plus className="h-3.5 w-3.5" />
            Add Voice
          </Button>
        )}
      </section>

      {/* Preview */}
      <section className="space-y-3 rounded-lg border p-4">
        <h3 className="text-sm font-medium">Preview</h3>
        <div className="flex items-center gap-3">
          {!previewUrl ? (
            <Button
              onClick={handlePreview}
              disabled={isPreviewing || components.length === 0}
              className="gap-2"
            >
              {isPreviewing ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" /> Generating…
                </>
              ) : (
                <>
                  <Blend className="h-4 w-4" /> Preview Blend
                </>
              )}
            </Button>
          ) : (
            <Button
              onClick={() => {
                if (audioRef.current) {
                  if (isPlayingPreview) {
                    audioRef.current.pause();
                  } else {
                    audioRef.current.play();
                  }
                }
              }}
              className="gap-2"
              variant={isPlayingPreview ? "outline" : "default"}
            >
              {isPlayingPreview ? (
                <>
                  <Pause className="h-4 w-4" /> Pause Preview
                </>
              ) : (
                <>
                  <Play className="h-4 w-4" /> Play Preview
                </>
              )}
            </Button>
          )}
          {previewUrl && (
            <audio
              ref={audioRef}
              src={previewUrl}
              autoPlay
              onPlay={() => setIsPlayingPreview(true)}
              onPause={() => setIsPlayingPreview(false)}
              onEnded={() => setIsPlayingPreview(false)}
              className="hidden"
            />
          )}
        </div>
      </section>

      {/* Save */}
      <section className="space-y-4 rounded-lg border p-4">
        <h3 className="text-sm font-medium flex items-center gap-2">
          <Save className="h-4 w-4" />
          Save as Custom Voice
        </h3>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">
              Display Name
            </label>
            <input
              value={saveName}
              onChange={(e) => {
                setSaveName(e.target.value);
                if (!saveId || saveId === idFromName(saveName)) {
                  setSaveId(idFromName(e.target.value));
                }
              }}
              placeholder="My Warm Voice"
              className="h-9 w-full rounded-md border bg-background px-3 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">
              Voice ID
            </label>
            <input
              value={saveId}
              onChange={(e) =>
                setSaveId(
                  e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""),
                )
              }
              placeholder="my_warm_voice"
              className="h-9 w-full rounded-md border bg-background px-3 font-mono text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">
              Gender
            </label>
            <select
              value={saveGender}
              onChange={(e) => setSaveGender(e.target.value)}
              className="h-9 w-full rounded-md border bg-background px-3 text-sm"
            >
              <option value="female">Female</option>
              <option value="male">Male</option>
            </select>
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">
              Language
            </label>
            <select
              value={saveLangCode}
              onChange={(e) => setSaveLangCode(e.target.value)}
              className="h-9 w-full rounded-md border bg-background px-3 text-sm"
            >
              <option value="a">American English</option>
              <option value="b">British English</option>
              <option value="j">Japanese</option>
              <option value="z">Mandarin</option>
              <option value="e">Spanish</option>
              <option value="f">French</option>
              <option value="h">Hindi</option>
              <option value="i">Italian</option>
              <option value="p">Portuguese</option>
            </select>
          </div>
        </div>
        <Button
          onClick={handleSave}
          disabled={isSaving || !saveName.trim() || !saveId.trim()}
          className="gap-2"
        >
          {isSaving ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" /> Saving…
            </>
          ) : (
            <>
              <Save className="h-4 w-4" /> Save Voice
            </>
          )}
        </Button>
      </section>
    </div>
  );
}

// ── Clone Voice Tab ─────────────────────────────────────────────────────────
//
// Voice cloning has no server-side API yet. The previous implementation here
// was a fake flow (sleep + clear form) that looked like success while doing
// nothing — replaced with an honest "coming soon" state until the engine
// actually supports cloning.

function CloneVoiceTab() {
  return (
    <div className="flex flex-col items-center justify-center gap-4 p-12 text-center">
      <AudioLines className="h-10 w-10 text-muted-foreground/40" />
      <div className="space-y-1">
        <h2 className="text-base font-semibold">Voice Cloning</h2>
        <p className="text-sm text-muted-foreground">
          Voice cloning is coming soon — not yet available in this build.
        </p>
        <p className="text-xs text-muted-foreground">
          In the meantime, you can create custom voices on the Blend tab or
          import community voice files on the Import tab.
        </p>
      </div>
    </div>
  );
}

// ── Import Voices Tab ─────────────────────────────────────────────────────────

function ImportVoicesTab({
  state,
  actions,
}: {
  state: ReturnType<typeof useTts>[0];
  actions: ReturnType<typeof useTts>[1];
}) {
  const isReady = state.status?.model_downloaded ?? false;
  const [customVoices, setCustomVoices] = useState<CustomVoiceInfo[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isImporting, setIsImporting] = useState(false);
  const [importName, setImportName] = useState("");
  const [importId, setImportId] = useState("");
  const [importGender, setImportGender] = useState("female");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    try {
      const voices = await listCustomVoices();
      setCustomVoices(voices);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleUse = useCallback(
    (voiceId: string) => {
      actions.setSelectedVoice(voiceId);
    },
    [actions],
  );

  const handleRename = useCallback(
    async (voiceId: string) => {
      if (!editName.trim()) return;
      try {
        // The engine returns 200 with {success:false,error} on failure —
        // check the body like handleImport does, don't rely on the throw.
        const result = await renameCustomVoice(voiceId, editName);
        if (!result.success) throw new Error(result.error ?? "Rename failed");
        setEditingId(null);
        await refresh();
        await actions.refreshVoices();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [editName, refresh, actions],
  );

  const handleDelete = useCallback(
    async (voiceId: string) => {
      try {
        const result = await deleteCustomVoice(voiceId);
        if (!result.success) throw new Error(result.error ?? "Delete failed");
        await refresh();
        await actions.refreshVoices();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [refresh, actions],
  );

  const handleImport = useCallback(async () => {
    if (!selectedFile || !importName.trim() || !importId.trim()) return;
    setIsImporting(true);
    setError(null);
    try {
      const result = await importVoiceFile({
        file: selectedFile,
        voice_id: importId,
        name: importName,
        gender: importGender,
      });
      if (!result.success) throw new Error(result.error ?? "Import failed");
      setSelectedFile(null);
      setImportName("");
      setImportId("");
      if (fileInputRef.current) fileInputRef.current.value = "";
      await refresh();
      await actions.refreshVoices();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setIsImporting(false);
    }
  }, [selectedFile, importName, importId, importGender, refresh, actions]);

  const idFromName = (n: string) =>
    n
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_|_$/g, "")
      .slice(0, 40);

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <h2 className="text-base font-semibold">Imported Voices</h2>
          <p className="text-xs text-muted-foreground">
            Manage voices you've imported from .npy / .bin community files or
            created via blending.
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="gap-1.5"
          onClick={refresh}
          disabled={isLoading}
        >
          <RefreshCw
            className={cn("h-3.5 w-3.5", isLoading && "animate-spin")}
          />
          Refresh
        </Button>
      </div>

      {error && (
        <div className="flex items-start gap-3 rounded-lg border border-red-500/20 bg-red-500/5 p-3">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-500" />
          <p className="flex-1 text-sm text-red-700 dark:text-red-400">{error}</p>
          <button
            onClick={() => setError(null)}
            className="text-red-700 dark:text-red-400 hover:text-red-300"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {/* Voice list */}
      {customVoices.length === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed p-10 text-center">
          <Blend className="h-8 w-8 text-muted-foreground/40" />
          <p className="text-sm text-muted-foreground">
            No custom voices yet. Create one on the Blend tab or import a .npy
            file below.
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {customVoices.map((v) => (
            <div
              key={v.voice_id}
              className="flex items-center gap-3 rounded-lg border bg-card/50 px-4 py-3"
            >
              <div
                className={cn(
                  "h-2 w-2 rounded-full shrink-0",
                  v.gender === "female" ? "bg-pink-400" : "bg-blue-400",
                )}
              />
              <div className="min-w-0 flex-1">
                {editingId === v.voice_id ? (
                  <div className="flex items-center gap-2">
                    <input
                      autoFocus
                      value={editName}
                      onChange={(e) => setEditName(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") void handleRename(v.voice_id);
                        if (e.key === "Escape") setEditingId(null);
                      }}
                      className="h-7 flex-1 rounded border bg-background px-2 text-sm focus:border-primary focus:outline-none"
                    />
                    <Button
                      size="sm"
                      className="h-7 px-2 text-xs"
                      onClick={() => handleRename(v.voice_id)}
                    >
                      Save
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-7 px-2 text-xs"
                      onClick={() => setEditingId(null)}
                    >
                      Cancel
                    </Button>
                  </div>
                ) : (
                  <>
                    <p className="truncate text-sm font-medium">{v.name}</p>
                    <p className="text-xs text-muted-foreground font-mono">
                      {v.voice_id}
                      {v.blend_recipe && v.blend_recipe.length > 0 && (
                        <span className="ml-2 not-italic">
                          · {v.blend_recipe.length} voices blended
                        </span>
                      )}
                    </p>
                  </>
                )}
              </div>
              <div className="flex items-center gap-1">
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 gap-1 px-2 text-xs"
                  onClick={() => handleUse(v.voice_id)}
                  disabled={!isReady}
                >
                  <AudioLines className="h-3 w-3" />
                  Use
                </Button>
                <button
                  onClick={() => {
                    setEditingId(v.voice_id);
                    setEditName(v.name);
                  }}
                  className="p-1.5 text-muted-foreground hover:text-foreground"
                >
                  <Pencil className="h-3.5 w-3.5" />
                </button>
                <button
                  onClick={() => void handleDelete(v.voice_id)}
                  className="p-1.5 text-muted-foreground hover:text-destructive"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Import section */}
      <section className="space-y-4 rounded-lg border p-4">
        <h3 className="text-sm font-medium flex items-center gap-2">
          <Upload className="h-4 w-4" />
          Import Voice File
        </h3>
        <p className="text-xs text-muted-foreground">
          Import a .npy or .bin voice embedding file from the community or a
          blending tool.
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5 sm:col-span-2">
            <label className="text-xs font-medium text-muted-foreground">
              File (.npy or .bin)
            </label>
            <input
              ref={fileInputRef}
              type="file"
              accept=".npy,.bin,.npz"
              onChange={(e) => {
                const f = e.target.files?.[0] ?? null;
                setSelectedFile(f);
                if (f && !importName) {
                  const base = f.name.replace(/\.[^.]+$/, "");
                  setImportName(
                    base
                      .replace(/_/g, " ")
                      .replace(/\b\w/g, (c) => c.toUpperCase()),
                  );
                  setImportId(idFromName(base));
                }
              }}
              className="h-9 w-full rounded-md border bg-background px-3 py-1.5 text-sm file:mr-3 file:rounded file:border-0 file:bg-primary/10 file:px-2 file:py-0.5 file:text-xs file:font-medium file:text-primary"
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">
              Display Name
            </label>
            <input
              value={importName}
              onChange={(e) => {
                setImportName(e.target.value);
                if (!importId || importId === idFromName(importName)) {
                  setImportId(idFromName(e.target.value));
                }
              }}
              placeholder="Community Voice"
              className="h-9 w-full rounded-md border bg-background px-3 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">
              Voice ID
            </label>
            <input
              value={importId}
              onChange={(e) =>
                setImportId(
                  e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""),
                )
              }
              placeholder="community_voice"
              className="h-9 w-full rounded-md border bg-background px-3 font-mono text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">
              Gender
            </label>
            <select
              value={importGender}
              onChange={(e) => setImportGender(e.target.value)}
              className="h-9 w-full rounded-md border bg-background px-3 text-sm"
            >
              <option value="female">Female</option>
              <option value="male">Male</option>
            </select>
          </div>
        </div>
        <Button
          onClick={handleImport}
          disabled={
            isImporting ||
            !selectedFile ||
            !importName.trim() ||
            !importId.trim()
          }
          className="gap-2"
        >
          {isImporting ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" /> Importing…
            </>
          ) : (
            <>
              <Upload className="h-4 w-4" /> Import Voice
            </>
          )}
        </Button>
      </section>

      {/* Community Resources */}
      <section className="space-y-3 rounded-lg border bg-card/50 p-4">
        <h3 className="text-sm font-medium flex items-center gap-2">
          <Star className="h-4 w-4 text-amber-500" />
          Find Great Voices
        </h3>
        <p className="text-xs text-muted-foreground leading-relaxed">
          Looking for more voices? The Kokoro TTS community has created
          thousands of high-quality voice packs (.npy / .bin) that you can
          import directly.
        </p>
        <div className="flex flex-col gap-2 pt-2">
          <a
            href="https://huggingface.co/spaces/hexgrad/Kokoro-TTS"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 text-sm text-primary hover:underline w-fit"
          >
            <ExternalLink className="h-3.5 w-3.5" />
            Official Kokoro HuggingFace Space
          </a>
          <a
            href="https://github.com/hexgrad/kokoro"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 text-sm text-primary hover:underline w-fit"
          >
            <ExternalLink className="h-3.5 w-3.5" />
            Kokoro GitHub Repository
          </a>
        </div>
      </section>
    </div>
  );
}

// ── Settings Tab ─────────────────────────────────────────────────────────────

function SettingsTab({
  state,
  actions,
}: {
  state: ReturnType<typeof useTts>[0];
  actions: ReturnType<typeof useTts>[1];
}) {
  if (!state.status) {
    return (
      <div className="flex items-center justify-center p-12">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const { status } = state;

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6 p-6">
      {/* Model Status */}
      <section className="space-y-4 rounded-lg border p-5">
        <h3 className="text-sm font-semibold">Model</h3>
        <div className="grid gap-4 sm:grid-cols-2">
          <InfoRow label="Status">
            {status.model_downloaded ? (
              <span className="flex items-center gap-1.5 text-emerald-500">
                <CheckCircle2 className="h-3.5 w-3.5" />
                Downloaded
              </span>
            ) : (
              <span className="flex items-center gap-1.5 text-amber-500">
                <AlertCircle className="h-3.5 w-3.5" />
                Not downloaded
              </span>
            )}
          </InfoRow>
          <InfoRow label="Loaded">{status.model_loaded ? "Yes" : "No"}</InfoRow>
          <InfoRow label="Model Directory">
            <span className="break-all font-mono text-xs opacity-70">
              {status.model_dir}
            </span>
          </InfoRow>
          <InfoRow label="Voices Available">{status.voice_count}</InfoRow>
        </div>
        <div className="flex gap-3 pt-2">
          {!status.model_downloaded && (
            <Button
              onClick={actions.downloadModel}
              disabled={state.isDownloading}
              size="sm"
              className="gap-2"
            >
              {state.isDownloading ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  Downloading... {status.download_progress.toFixed(0)}%
                </>
              ) : (
                <>
                  <Download className="h-3.5 w-3.5" />
                  Download Model (~300 MB)
                </>
              )}
            </Button>
          )}
          {status.model_loaded && (
            <Button
              onClick={actions.unload}
              variant="outline"
              size="sm"
              className="gap-2"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Unload from Memory
            </Button>
          )}
        </div>
      </section>

      {/* Defaults */}
      <section className="space-y-4 rounded-lg border p-5">
        <h3 className="text-sm font-semibold">Defaults</h3>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">
              Default Voice
            </label>
            <VoiceSelector
              voices={state.voices}
              selected={state.selectedVoice}
              onChange={actions.setSelectedVoice}
            />
          </div>
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <label className="text-xs font-medium text-muted-foreground">
                Default Speed
              </label>
              <span className="text-xs tabular-nums text-muted-foreground">
                {state.speed.toFixed(1)}x
              </span>
            </div>
            <Slider
              min={0.5}
              max={2}
              step={0.05}
              value={[state.speed]}
              onValueChange={([v]) => {
                if (v !== undefined) actions.setSpeed(v);
              }}
            />
          </div>
        </div>
      </section>

      {/* About */}
      <section className="space-y-2 rounded-lg border p-5">
        <h3 className="text-sm font-semibold">About</h3>
        <p className="text-xs leading-relaxed text-muted-foreground">
          Kokoro is an open-weight TTS model with 82 million parameters. Despite
          its lightweight architecture, it delivers quality comparable to larger
          models while running 3-5x faster than real-time on CPU. Apache 2.0
          licensed.
        </p>
        <div className="flex gap-3 pt-1">
          <a
            href="https://huggingface.co/hexgrad/Kokoro-82M"
            target="_blank"
            rel="noreferrer"
            className="text-xs text-primary hover:underline"
          >
            HuggingFace Model Card
          </a>
          <a
            href="https://github.com/thewh1teagle/kokoro-onnx"
            target="_blank"
            rel="noreferrer"
            className="text-xs text-primary hover:underline"
          >
            kokoro-onnx GitHub
          </a>
        </div>
      </section>
    </div>
  );
}

function InfoRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-0.5">
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </div>
  );
}

// ── Shared Banners ───────────────────────────────────────────────────────────

function DownloadBanner({
  status,
  isDownloading,
  onDownload,
}: {
  status: { download_progress: number; is_downloading: boolean };
  isDownloading: boolean;
  onDownload: () => void;
}) {
  return (
    <div className="rounded-lg border border-blue-500/20 bg-blue-500/5 p-4">
      <div className="flex items-center gap-4">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-blue-500/10">
          <Download className="h-5 w-5 text-blue-500" />
        </div>
        <div className="flex-1">
          <h4 className="text-sm font-medium">Model Download Required</h4>
          <p className="mt-0.5 text-xs text-muted-foreground">
            The Kokoro TTS model (~300 MB) needs to be downloaded before you can
            generate speech.
          </p>
          {status.is_downloading && (
            <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-blue-500 transition-all"
                style={{ width: `${status.download_progress}%` }}
              />
            </div>
          )}
        </div>
        <Button
          onClick={onDownload}
          disabled={isDownloading}
          size="sm"
          variant="outline"
          className="shrink-0 gap-2"
        >
          {isDownloading ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              {status.download_progress.toFixed(0)}%
            </>
          ) : (
            <>
              <Download className="h-3.5 w-3.5" />
              Download
            </>
          )}
        </Button>
      </div>
    </div>
  );
}
