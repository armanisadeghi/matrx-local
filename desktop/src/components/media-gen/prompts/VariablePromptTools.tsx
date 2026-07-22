import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentProps,
  type ReactNode,
  type RefObject,
} from "react";
import { ChevronDown, Eye, RefreshCw, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Textarea } from "@/components/ui/textarea";
import { useListLibraryApp } from "@/contexts/ListLibraryContext";
import type { NamedList } from "@/lib/list-library/types";
import {
  insertListVariableToken,
  listsMatchingVariableName,
  sampleListValues,
  variableNameForList,
  variableTokenForList,
  type SampledListValue,
  type SampledListValues,
} from "@/lib/list-variables";
import { findTokens, variableKey } from "@/lib/prompt-matrix";
import { cn } from "@/lib/utils";

type TextareaProps = Omit<ComponentProps<"textarea">, "value" | "onChange">;

interface SavedSelection {
  start: number;
  end: number;
}

interface VariablePromptFieldContextValue {
  value: string;
  onChange: (value: string) => void;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  rememberSelection: () => void;
  saveSelection: (start: number, end: number) => void;
  insertList: (list: NamedList) => void;
  lists: readonly NamedList[];
}

const VariablePromptFieldContext =
  createContext<VariablePromptFieldContextValue | null>(null);

function useVariablePromptFieldContext(): VariablePromptFieldContextValue {
  const ctx = useContext(VariablePromptFieldContext);
  if (!ctx) {
    throw new Error(
      "Variable prompt controls must be used inside VariablePromptField",
    );
  }
  return ctx;
}

/** Shared state for a prompt textarea + its Insert variable control. */
export function VariablePromptField({
  value,
  onChange,
  onVariableInsert,
  children,
}: {
  value: string;
  onChange: (value: string) => void;
  onVariableInsert?: (list: NamedList, value: string) => void;
  children: ReactNode;
}) {
  const [listState] = useListLibraryApp();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const selectionRef = useRef<SavedSelection>({
    start: value.length,
    end: value.length,
  });

  const saveSelection = useCallback((start: number, end: number) => {
    selectionRef.current = { start, end };
  }, []);

  const rememberSelection = useCallback(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    selectionRef.current = {
      start: textarea.selectionStart,
      end: textarea.selectionEnd,
    };
  }, []);

  const insertList = useCallback(
    (list: NamedList) => {
      const selection = selectionRef.current;
      const insertion = insertListVariableToken(
        value,
        list.name,
        selection.start,
        selection.end,
      );
      if (insertion === null) return;

      if (onVariableInsert) onVariableInsert(list, insertion.text);
      else onChange(insertion.text);
      selectionRef.current = {
        start: insertion.cursor,
        end: insertion.cursor,
      };

      window.requestAnimationFrame(() => {
        const textarea = textareaRef.current;
        if (!textarea) return;
        textarea.focus();
        textarea.setSelectionRange(insertion.cursor, insertion.cursor);
      });
    },
    [onChange, onVariableInsert, value],
  );

  const contextValue = useMemo(
    (): VariablePromptFieldContextValue => ({
      value,
      onChange,
      textareaRef,
      rememberSelection,
      saveSelection,
      insertList,
      lists: listState.lists,
    }),
    [
      insertList,
      listState.lists,
      onChange,
      rememberSelection,
      saveSelection,
      value,
    ],
  );

  return (
    <VariablePromptFieldContext.Provider value={contextValue}>
      {children}
    </VariablePromptFieldContext.Provider>
  );
}

/** Searchable saved-list variable inserter (place anywhere beside its field). */
export function VariableInsertButton({ className }: { className?: string }) {
  const { lists, rememberSelection, insertList } =
    useVariablePromptFieldContext();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  const filteredLists = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return lists;
    return lists.filter((list) => {
      const variableName = variableNameForList(list.name) ?? "";
      return (
        list.name.toLowerCase().includes(needle) ||
        variableName.toLowerCase().includes(needle)
      );
    });
  }, [lists, query]);

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setQuery("");
      }}
    >
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className={cn("h-9 gap-1.5 px-2.5 text-xs", className)}
          disabled={lists.length === 0}
          onMouseDown={rememberSelection}
        >
          <span className="font-mono text-[11px]">{"{{ }}"}</span>
          Insert variable
          <ChevronDown className="h-3 w-3 text-muted-foreground" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        className="w-[min(420px,calc(100vw-2rem))] p-0"
        onCloseAutoFocus={(event) => event.preventDefault()}
      >
        <div className="border-b p-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search saved lists…"
              className="h-8 pl-8 text-xs"
              autoFocus
            />
          </div>
        </div>
        <div className="max-h-72 overflow-y-auto p-1.5">
          {filteredLists.length === 0 ? (
            <p className="px-3 py-6 text-center text-xs text-muted-foreground">
              No matching lists.
            </p>
          ) : (
            filteredLists.map((list) => {
              const token = variableTokenForList(list.name);
              const optionCount = list.options.filter(
                (option) => option.enabled && option.value.trim().length > 0,
              ).length;
              return (
                <button
                  key={list.id}
                  type="button"
                  className="flex w-full items-center gap-3 rounded-md px-2.5 py-2 text-left hover:bg-accent focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                  disabled={token === null}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => {
                    insertList(list);
                    setOpen(false);
                    setQuery("");
                  }}
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-foreground">
                      {list.name}
                    </p>
                    <p className="text-[11px] text-muted-foreground">
                      {optionCount} option{optionCount === 1 ? "" : "s"}
                    </p>
                  </div>
                  <code className="shrink-0 rounded bg-violet-500/10 px-1.5 py-1 text-[11px] text-violet-700 dark:text-violet-300">
                    {token ?? "Rename to use"}
                  </code>
                </button>
              );
            })
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}

/** Textarea bound to the surrounding VariablePromptField. */
export function VariablePromptInput({ className, ...props }: TextareaProps) {
  const { value, onChange, textareaRef, rememberSelection, saveSelection } =
    useVariablePromptFieldContext();

  return (
    <Textarea
      ref={textareaRef}
      value={value}
      onChange={(event) => {
        onChange(event.target.value);
        saveSelection(event.target.selectionStart, event.target.selectionEnd);
      }}
      onSelect={rememberSelection}
      onKeyUp={rememberSelection}
      onClick={rememberSelection}
      className={className}
      {...props}
    />
  );
}

export interface VariablePromptTextareaProps extends TextareaProps {
  value: string;
  onChange: (value: string) => void;
  onVariableInsert?: (list: NamedList, value: string) => void;
}

/** A textarea with a searchable, saved-list-backed variable inserter above it. */
export function VariablePromptTextarea({
  value,
  onChange,
  onVariableInsert,
  className,
  ...props
}: VariablePromptTextareaProps) {
  return (
    <VariablePromptField
      value={value}
      onChange={onChange}
      {...(onVariableInsert ? { onVariableInsert } : {})}
    >
      <div className="flex min-h-0 flex-col gap-1.5">
        <div className="flex shrink-0 items-center justify-end">
          <VariableInsertButton className="h-7 px-2" />
        </div>
        <VariablePromptInput className={className} {...props} />
      </div>
    </VariablePromptField>
  );
}

export interface PromptPreviewField {
  label: string;
  text: string;
}

function findMappedList(
  tokenName: string,
  lists: readonly NamedList[],
  listIdByVariable?: Readonly<Record<string, string>>,
): { list: NamedList | null; issue: string | null } {
  const key = variableKey(tokenName);
  if (listIdByVariable !== undefined) {
    const mapping = Object.entries(listIdByVariable).find(
      ([name]) => variableKey(name) === key,
    );
    if (mapping !== undefined) {
      const list = lists.find((candidate) => candidate.id === mapping[1]);
      return list
        ? { list, issue: null }
        : { list: null, issue: `{{${tokenName}}} is not mapped to a list.` };
    }
  }

  const matches = listsMatchingVariableName(lists, tokenName);
  if (matches.length === 1) return { list: matches[0] ?? null, issue: null };
  if (matches.length > 1) {
    return {
      list: null,
      issue: `{{${tokenName}}} matches more than one saved list. Rename one or map it explicitly.`,
    };
  }
  return {
    list: null,
    issue: `{{${tokenName}}} has no matching saved list.`,
  };
}

function renderPreviewText({
  text,
  lists,
  samples,
  listIdByVariable,
}: {
  text: string;
  lists: readonly NamedList[];
  samples: SampledListValues;
  listIdByVariable?: Readonly<Record<string, string>>;
}): { content: ReactNode; issues: string[] } {
  const tokens = findTokens(text);
  if (tokens.length === 0) {
    return {
      content: <span>{text || "Nothing to preview."}</span>,
      issues: [],
    };
  }

  const parts: ReactNode[] = [];
  const issues: string[] = [];
  let cursor = 0;
  tokens.forEach((token, index) => {
    parts.push(text.slice(cursor, token.start));
    const resolved = findMappedList(token.name, lists, listIdByVariable);
    const sample = resolved.list ? samples.get(resolved.list.id) : undefined;
    if (sample) {
      parts.push(
        <span
          key={`${token.start}:${index}`}
          className="rounded bg-violet-500/15 px-0.5 font-medium text-violet-700 dark:text-violet-300"
          title={`${token.name} · ${sample.listName}`}
        >
          {sample.value}
        </span>,
      );
    } else {
      const issue =
        resolved.issue ??
        `{{${token.name}}} has no enabled, non-empty list options.`;
      issues.push(issue);
      parts.push(
        <span
          key={`${token.start}:${index}`}
          className="rounded bg-amber-500/15 px-0.5 font-medium text-amber-700 dark:text-amber-300"
          title={issue}
        >
          {text.slice(token.start, token.end)}
        </span>,
      );
    }
    cursor = token.end;
  });
  parts.push(text.slice(cursor));
  return { content: <>{parts}</>, issues };
}

/** Inline test rendering with sampled list values highlighted in context. */
export function PromptVariablePreview({
  fields,
  listIdByVariable,
  className,
}: {
  fields: readonly PromptPreviewField[];
  listIdByVariable?: Readonly<Record<string, string>>;
  className?: string;
}) {
  const [listState] = useListLibraryApp();
  const [open, setOpen] = useState(false);
  const [samples, setSamples] = useState<Map<string, SampledListValue>>(
    new Map(),
  );

  const reroll = useCallback(() => {
    setSamples((previous) => sampleListValues(listState.lists, previous));
  }, [listState.lists]);

  const listSignature = useMemo(
    () =>
      listState.lists
        .map((list) =>
          [
            list.id,
            list.name,
            ...list.options.map(
              (option) =>
                `${option.id}:${option.enabled ? 1 : 0}:${option.value}`,
            ),
          ].join("\u0000"),
        )
        .join("\u0001"),
    [listState.lists],
  );

  useEffect(() => {
    if (!open) return;
    setSamples((previous) => sampleListValues(listState.lists, previous));
  }, [open, listSignature, listState.lists]);

  const hasText = fields.some((field) => field.text.length > 0);
  const renderedFields = fields
    .filter((field) => field.text.length > 0)
    .map((field) => ({
      ...field,
      preview: renderPreviewText({
        text: field.text,
        lists: listState.lists,
        samples,
        ...(listIdByVariable ? { listIdByVariable } : {}),
      }),
    }));
  const issues = new Set(
    renderedFields.flatMap((field) => field.preview.issues),
  );

  return (
    <div className={cn("space-y-2", className)}>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-8 gap-1.5 text-xs"
          disabled={!hasText}
          onClick={() => setOpen((current) => !current)}
          aria-expanded={open}
        >
          <Eye className="h-3.5 w-3.5" />
          {open ? "Hide test preview" : "Test with list values"}
        </Button>
        {open && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-8 gap-1.5 text-xs"
            onClick={reroll}
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Try another
          </Button>
        )}
      </div>

      {open && (
        <div className="space-y-3 rounded-lg border border-violet-500/30 bg-violet-500/5 p-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-xs font-medium text-foreground">
                Test preview
              </p>
              <p className="text-[11px] text-muted-foreground">
                Highlighted text came from a saved list. The prompt itself is
                unchanged.
              </p>
            </div>
            <span className="shrink-0 rounded bg-violet-500/15 px-1.5 py-0.5 text-[10px] font-medium text-violet-700 dark:text-violet-300">
              Dynamic text
            </span>
          </div>
          {renderedFields.map((field) => (
            <div key={field.label} className="space-y-1">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                {field.label}
              </p>
              <div className="whitespace-pre-wrap break-words rounded-md border bg-background px-3 py-2 text-sm leading-relaxed text-foreground">
                {field.preview.content}
              </div>
            </div>
          ))}
          {issues.size > 0 && (
            <ul className="space-y-1 text-[11px] text-amber-700 dark:text-amber-300">
              {[...issues].map((issue) => (
                <li key={issue}>• {issue}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
