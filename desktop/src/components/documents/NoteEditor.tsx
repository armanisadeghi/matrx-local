import {
  useState,
  useCallback,
  useRef,
  useEffect,
  useDeferredValue,
  memo,
} from "react";
import {
  Eye,
  Pencil,
  Bold,
  Italic,
  List,
  ListOrdered,
  Link,
  Code,
  Heading1,
  Heading2,
  Heading3,
  Quote,
  Minus,
  ImageIcon,
  Save,
  Loader2,
  Mic,
  Check,
  X,
  FileWarning,
} from "lucide-react";
import { RecordingMicButton } from "@/components/recording/RecordingMicButton";
import { RmsLevelBar } from "@/components/recording/RmsLevelBar";
import { cn } from "@/lib/utils";
import type { DocNote } from "@/lib/api";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useTranscriptionApp } from "@/contexts/TranscriptionContext";
import { usePermissionsContext } from "@/contexts/PermissionsContext";

interface NoteEditorProps {
  note: DocNote;
  saving: boolean;
  onChange: (content: string) => void;
  onLabelChange: (label: string) => void;
}

type ViewMode = "edit" | "preview" | "split";

/**
 * Above this size the live preview stops auto-rendering. Parsing + rendering
 * markdown is synchronous main-thread work; a large paste in split view froze
 * the whole webview long enough for the OS "wait or quit" dialog (the
 * 2026-07 notes freeze). ~128 KB parses in well under a frame budget's worth
 * of tolerance on target hardware; beyond it the user opts in explicitly.
 */
const PREVIEW_AUTO_RENDER_LIMIT = 128 * 1024;

/**
 * Memoized markdown preview. Two properties matter:
 * - `memo` — toolbar/saving/dictation re-renders don't re-parse the document.
 * - callers pass a DEFERRED content value, so keystrokes update the textarea
 *   at input priority and the (expensive) re-parse happens at background
 *   priority, coalescing rapid keystrokes into one parse.
 */
const NotePreview = memo(function NotePreview({ content }: { content: string }) {
  return (
    <div className="prose prose-sm dark:prose-invert max-w-none">
      <Markdown remarkPlugins={[remarkGfm]}>{content}</Markdown>
    </div>
  );
});

export function NoteEditor({
  note,
  saving,
  onChange,
  onLabelChange,
}: NoteEditorProps) {
  const [viewMode, setViewMode] = useState<ViewMode>("split");
  const [content, setContent] = useState(note.content ?? "");
  const [label, setLabel] = useState(note.label);
  const [forcePreview, setForcePreview] = useState(false);
  // Deferred: keystrokes commit to the textarea immediately; the markdown
  // re-parse runs afterwards at background priority (see NotePreview).
  const previewContent = useDeferredValue(content);
  const previewBlocked =
    content.length > PREVIEW_AUTO_RENDER_LIMIT && !forcePreview;
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const prevNoteIdRef = useRef(note.id);
  const labelDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Inline dictation state
  const [showDictation, setShowDictation] = useState(false);
  const [dictationText, setDictationText] = useState("");
  const { state: transcriptionState, actions: transcriptionActions } =
    useTranscriptionApp();
  const { check, request } = usePermissionsContext();
  const prevSegmentCountRef = useRef(0);

  // Accumulate live segments into dictationText
  useEffect(() => {
    if (!showDictation) return;
    if (transcriptionState.segments.length > prevSegmentCountRef.current) {
      const newSegs = transcriptionState.segments.slice(
        prevSegmentCountRef.current,
      );
      prevSegmentCountRef.current = transcriptionState.segments.length;
      const newText = newSegs
        .map((s) => s.text)
        .filter((t) => t.length > 0)
        .join(" ");
      if (newText) {
        setDictationText((prev) => (prev ? prev + " " + newText : newText));
      }
    }
  }, [transcriptionState.segments, showDictation]);

  const handleOpenDictation = useCallback(async () => {
    const status = await check("microphone");
    if (status === "not_determined") {
      await request("microphone");
    }
    setDictationText("");
    prevSegmentCountRef.current = 0;
    setShowDictation(true);
  }, [check, request]);

  const handleStartDictation = useCallback(async () => {
    setDictationText("");
    prevSegmentCountRef.current = 0;
    await transcriptionActions.startRecording();
  }, [transcriptionActions]);

  const handleStopDictation = useCallback(async () => {
    await transcriptionActions.stopRecording();
  }, [transcriptionActions]);

  const handleInsertDictation = useCallback(() => {
    if (!dictationText.trim()) {
      setShowDictation(false);
      return;
    }
    const ta = textareaRef.current;
    let newContent: string;
    if (ta) {
      const start = ta.selectionStart;
      const end = ta.selectionEnd;
      const separator = start > 0 && content[start - 1] !== "\n" ? "\n\n" : "";
      newContent =
        content.substring(0, start) +
        separator +
        dictationText.trim() +
        "\n\n" +
        content.substring(end);
    } else {
      newContent = content
        ? content + "\n\n" + dictationText.trim()
        : dictationText.trim();
    }
    contentRef.current = newContent;
    setContent(newContent);
    onChange(newContent);
    setShowDictation(false);
    setDictationText("");
  }, [dictationText, content, onChange]);

  const handleCancelDictation = useCallback(async () => {
    if (transcriptionState.isRecording) {
      await transcriptionActions.stopRecording();
    }
    setShowDictation(false);
    setDictationText("");
    prevSegmentCountRef.current = 0;
  }, [transcriptionState.isRecording, transcriptionActions]);

  // Sync state when the active note switches — and also when the SAME note's
  // content changes underneath us (a realtime remote edit pulled by
  // Documents' reload). Ignoring same-id updates left the editor showing
  // stale text, and the next local keystroke pushed that stale content back,
  // clobbering the remote edit. Only resync when the user has no unsaved
  // local divergence (lastSyncedContentRef tracks what we last loaded;
  // contentRef mirrors the textarea state so the decision is synchronous —
  // deciding inside a setState updater forced side effects like the label
  // reset to run unconditionally, clobbering in-progress renames).
  const lastSyncedContentRef = useRef<string>(note.content ?? "");
  const contentRef = useRef<string>(note.content ?? "");
  useEffect(() => {
    if (note.id !== prevNoteIdRef.current) {
      // Cancel any pending label debounce from the previous note before
      // switching — we don't want a stale rename firing on the new note.
      if (labelDebounceRef.current) {
        clearTimeout(labelDebounceRef.current);
        labelDebounceRef.current = null;
      }
      setContent(note.content ?? "");
      setLabel(note.label);
      setForcePreview(false);
      lastSyncedContentRef.current = note.content ?? "";
      contentRef.current = note.content ?? "";
      prevNoteIdRef.current = note.id;
      return;
    }
    const incoming = note.content ?? "";
    if (incoming === lastSyncedContentRef.current) return;

    if (incoming === contentRef.current) {
      // Echo of our own debounced save round-tripping — just advance the
      // synced marker so future remote edits are still recognized.
      lastSyncedContentRef.current = incoming;
      return;
    }
    if (contentRef.current === lastSyncedContentRef.current) {
      // Genuine remote change and no local divergence — apply it.
      lastSyncedContentRef.current = incoming;
      contentRef.current = incoming;
      setContent(incoming);
      // Only follow the remote label when the user isn't mid-rename.
      if (!labelDebounceRef.current) {
        setLabel(note.label);
      }
    }
    // Otherwise the user is mid-edit: their draft (and its upcoming save or
    // the conflict path) wins; do not touch content or label.
  }, [note.id, note.content, note.label]);

  const handleContentChange = useCallback(
    (value: string) => {
      contentRef.current = value;
      setContent(value);
      onChange(value);
    },
    [onChange],
  );

  // Debounced label change — updates local display immediately, but only
  // propagates to the parent (and therefore to the API) after the user
  // pauses for 600 ms. This prevents one HTTP PUT per keystroke (the
  // 21-copies bug) while keeping the title field responsive.
  const handleLabelChange = useCallback(
    (value: string) => {
      setLabel(value);
      if (labelDebounceRef.current) clearTimeout(labelDebounceRef.current);
      labelDebounceRef.current = setTimeout(() => {
        labelDebounceRef.current = null;
        onLabelChange(value);
      }, 600);
    },
    [onLabelChange],
  );

  // Fire the rename immediately when the user leaves the title field so
  // they don't have to wait for the debounce to settle.
  const handleLabelBlur = useCallback(() => {
    if (labelDebounceRef.current) {
      clearTimeout(labelDebounceRef.current);
      labelDebounceRef.current = null;
      onLabelChange(label);
    }
  }, [label, onLabelChange]);

  // Toolbar insert helpers
  const insertMarkdown = useCallback(
    (before: string, after = "", placeholder = "") => {
      const ta = textareaRef.current;
      if (!ta) return;

      const start = ta.selectionStart;
      const end = ta.selectionEnd;
      const selected = content.substring(start, end) || placeholder;
      const newContent =
        content.substring(0, start) +
        before +
        selected +
        after +
        content.substring(end);

      contentRef.current = newContent;
      setContent(newContent);
      onChange(newContent);

      // Restore cursor
      requestAnimationFrame(() => {
        ta.focus();
        const cursorPos = start + before.length + selected.length;
        ta.setSelectionRange(cursorPos, cursorPos);
      });
    },
    [content, onChange],
  );

  const toolbarButtons = [
    {
      icon: Bold,
      action: () => insertMarkdown("**", "**", "bold"),
      title: "Bold",
    },
    {
      icon: Italic,
      action: () => insertMarkdown("*", "*", "italic"),
      title: "Italic",
    },
    {
      icon: Code,
      action: () => insertMarkdown("`", "`", "code"),
      title: "Inline Code",
    },
    { type: "separator" as const },
    {
      icon: Heading1,
      action: () => insertMarkdown("# ", "", "Heading"),
      title: "H1",
    },
    {
      icon: Heading2,
      action: () => insertMarkdown("## ", "", "Heading"),
      title: "H2",
    },
    {
      icon: Heading3,
      action: () => insertMarkdown("### ", "", "Heading"),
      title: "H3",
    },
    { type: "separator" as const },
    {
      icon: List,
      action: () => insertMarkdown("- ", "", "item"),
      title: "Bullet List",
    },
    {
      icon: ListOrdered,
      action: () => insertMarkdown("1. ", "", "item"),
      title: "Numbered List",
    },
    {
      icon: Quote,
      action: () => insertMarkdown("> ", "", "quote"),
      title: "Quote",
    },
    { icon: Minus, action: () => insertMarkdown("\n---\n"), title: "Divider" },
    { type: "separator" as const },
    {
      icon: Link,
      action: () => insertMarkdown("[", "](url)", "text"),
      title: "Link",
    },
    {
      icon: ImageIcon,
      action: () => insertMarkdown("![", "](url)", "alt"),
      title: "Image",
    },
    {
      icon: Code,
      action: () => insertMarkdown("\n```\n", "\n```\n", "code"),
      title: "Code Block",
    },
  ];

  return (
    <div className="flex flex-col h-full">
      {/* Header: title + view mode toggle */}
      <div className="flex items-center gap-3 border-b px-4 py-2">
        <input
          value={label}
          onChange={(e) => handleLabelChange(e.target.value)}
          onBlur={handleLabelBlur}
          className="flex-1 bg-transparent text-lg font-semibold outline-none"
          placeholder="Note title..."
        />
        <div className="flex items-center gap-1">
          {saving && (
            <span className="flex items-center gap-1 text-xs text-muted-foreground mr-2">
              <Loader2 className="h-3 w-3 animate-spin" />
              Saving...
            </span>
          )}
          {!saving && (
            <span className="flex items-center gap-1 text-xs text-emerald-500 mr-2">
              <Save className="h-3 w-3" />
              Saved
            </span>
          )}
          <button
            onClick={() => setViewMode("edit")}
            className={cn(
              "rounded-md px-2 py-1 text-xs transition-colors",
              viewMode === "edit"
                ? "bg-primary/15 text-primary"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <Pencil className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={() => setViewMode("split")}
            className={cn(
              "rounded-md px-2 py-1 text-xs transition-colors",
              viewMode === "split"
                ? "bg-primary/15 text-primary"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            Split
          </button>
          <button
            onClick={() => setViewMode("preview")}
            className={cn(
              "rounded-md px-2 py-1 text-xs transition-colors",
              viewMode === "preview"
                ? "bg-primary/15 text-primary"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <Eye className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {/* Toolbar */}
      {viewMode !== "preview" && (
        <div className="flex items-center gap-0.5 border-b px-3 py-1.5 overflow-x-auto">
          {toolbarButtons.map((btn, i) =>
            "type" in btn && btn.type === "separator" ? (
              <div key={i} className="w-px h-4 bg-border mx-1" />
            ) : (
              <button
                key={i}
                onClick={"action" in btn ? btn.action : undefined}
                title={"title" in btn ? btn.title : ""}
                className="rounded p-1.5 text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
              >
                {"icon" in btn && <btn.icon className="h-3.5 w-3.5" />}
              </button>
            ),
          )}
          {/* Dictation button — only shown when voice setup is complete */}
          {transcriptionState.setupStatus?.setup_complete && (
            <>
              <div className="w-px h-4 bg-border mx-1" />
              <button
                onClick={handleOpenDictation}
                title="Dictate into note"
                className={cn(
                  "rounded p-1.5 transition-colors",
                  showDictation
                    ? "text-red-500 bg-red-500/10"
                    : "text-muted-foreground hover:text-foreground hover:bg-accent",
                )}
              >
                <Mic className="h-3.5 w-3.5" />
              </button>
            </>
          )}
        </div>
      )}

      {/* Inline dictation panel */}
      {showDictation && (
        <div className="border-b bg-muted/30 px-4 py-3 space-y-2">
          <div className="flex items-center gap-2">
            <RecordingMicButton
              isRecording={transcriptionState.isRecording}
              isProcessingTail={transcriptionState.isProcessingTail}
              liveRms={transcriptionState.liveRms}
              onToggle={
                transcriptionState.isRecording
                  ? handleStopDictation
                  : handleStartDictation
              }
              disabled={!transcriptionState.activeModel}
              size="xs"
            />

            {transcriptionState.isRecording && (
              <div className="flex-1">
                <RmsLevelBar
                  liveRms={transcriptionState.liveRms}
                  height="sm"
                  showDot
                />
              </div>
            )}
            {!transcriptionState.isRecording &&
              !transcriptionState.isProcessingTail && (
                <span className="text-xs text-muted-foreground flex-1">
                  {transcriptionState.activeModel
                    ? dictationText
                      ? "Click the mic to keep recording, or insert below"
                      : "Click the mic to start dictating"
                    : "Voice model not loaded. Go to Speech to Text → Setup first."}
                </span>
              )}
            {transcriptionState.isProcessingTail && (
              <span className="text-xs text-amber-500 flex-1">Finishing…</span>
            )}

            {/* Insert / Cancel */}
            <button
              onClick={handleInsertDictation}
              disabled={!dictationText.trim()}
              className="flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium bg-primary/10 text-primary hover:bg-primary/20 transition-colors disabled:opacity-40 disabled:cursor-not-allowed shrink-0"
            >
              <Check className="h-3 w-3" />
              Insert
            </button>
            <button
              onClick={handleCancelDictation}
              className="flex items-center gap-1 rounded-md px-2.5 py-1 text-xs text-muted-foreground hover:text-foreground hover:bg-muted transition-colors shrink-0"
            >
              <X className="h-3 w-3" />
              Cancel
            </button>
          </div>

          {/* Dictation text preview */}
          {dictationText && (
            <div className="rounded-md border bg-background/60 px-3 py-2">
              <p className="text-sm leading-relaxed text-foreground/90">
                {dictationText}
              </p>
            </div>
          )}
        </div>
      )}

      {/* Editor / Preview */}
      <div className="flex-1 flex overflow-hidden">
        {(viewMode === "edit" || viewMode === "split") && (
          <div
            className={cn(
              "flex-1 overflow-hidden",
              viewMode === "split" && "border-r",
            )}
          >
            <textarea
              ref={textareaRef}
              value={content}
              onChange={(e) => handleContentChange(e.target.value)}
              className="h-full w-full resize-none bg-transparent p-4 font-mono text-sm outline-none"
              placeholder="Start writing..."
              spellCheck={false}
            />
          </div>
        )}

        {(viewMode === "preview" || viewMode === "split") && (
          <div
            className={cn(
              "flex-1 overflow-auto p-4 transition-opacity",
              previewContent !== content && "opacity-60",
            )}
          >
            {previewBlocked ? (
              <div className="flex h-full flex-col items-center justify-center gap-3 text-center text-muted-foreground">
                <FileWarning className="h-8 w-8 opacity-40" />
                <p className="text-sm">
                  Live preview paused — this note is{" "}
                  {Math.round(content.length / 1024)} KB and rendering it on
                  every keystroke would freeze the editor.
                </p>
                <button
                  onClick={() => setForcePreview(true)}
                  className="rounded-md bg-primary/10 px-3 py-1.5 text-sm text-primary hover:bg-primary/20"
                >
                  Render preview anyway
                </button>
              </div>
            ) : (
              <NotePreview content={previewContent} />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
