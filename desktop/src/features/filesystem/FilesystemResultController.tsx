import { useCallback, useEffect, useState } from "react";
import { engine } from "@/lib/api";
import { FilesystemResultView } from "./FilesystemResultView";
import { normalizeFilesystemPayload } from "./tool-results";
import type {
  FilesystemDirectoryPage,
  FilesystemEntry,
  FilesystemResult,
  FilesystemSearchPage,
} from "./types";

export interface FilesystemResultControllerProps {
  result: FilesystemResult;
  onReference?: (paths: string[]) => void;
}

type PageableResult = FilesystemDirectoryPage | FilesystemSearchPage;

function pageIdentity(page: PageableResult): string {
  return page.kind === "filesystem.directory-page"
    ? `${page.kind}:${page.namespace}:${page.path}`
    : `${page.kind}:${page.namespace}:${page.root ?? ""}:${page.query}`;
}

export function appendFilesystemPage(current: PageableResult, next: PageableResult): PageableResult {
  if (pageIdentity(current) !== pageIdentity(next)) return current;
  const byPath = new Map(current.entries.map((entry) => [entry.path, entry]));
  for (const entry of next.entries) byPath.set(entry.path, entry);
  return {
    ...current,
    ...next,
    entries: [...byPath.values()],
  };
}

function requireDirectoryPage(payload: unknown): FilesystemDirectoryPage {
  const result = normalizeFilesystemPayload(payload);
  if (result?.kind !== "filesystem.directory-page") {
    throw new Error("The engine returned an invalid directory page.");
  }
  return result;
}

function requirePageableResult(payload: unknown): PageableResult {
  const result = normalizeFilesystemPayload(payload);
  if (result?.kind !== "filesystem.directory-page" && result?.kind !== "filesystem.search-page") {
    throw new Error("The engine returned an invalid filesystem page.");
  }
  return result;
}

/** Connect the canonical renderer to direct engine paging and lazy child loading. */
export function FilesystemResultController({ result, onReference }: FilesystemResultControllerProps) {
  const [current, setCurrent] = useState(result);
  const [loadingMore, setLoadingMore] = useState(false);
  const [pagingError, setPagingError] = useState<string | null>(null);

  useEffect(() => {
    setCurrent(result);
    setPagingError(null);
  }, [result]);

  const loadChildren = useCallback(async (entry: FilesystemEntry): Promise<FilesystemEntry[]> => {
    const page = requireDirectoryPage(await engine.listFilesystem(entry.path, { limit: 100 }));
    return page.entries;
  }, []);

  const loadMore = useCallback(async (cursor: string): Promise<void> => {
    if (current.kind !== "filesystem.directory-page" && current.kind !== "filesystem.search-page") return;
    if (loadingMore) return;
    setLoadingMore(true);
    setPagingError(null);
    try {
      const payload = current.kind === "filesystem.directory-page"
        ? await engine.listFilesystem(current.path, { cursor, limit: 100 })
        : await engine.findFilesystem(current.query, {
            ...(current.root ? { root: current.root } : {}),
            cursor,
            limit: 100,
          });
      const next = requirePageableResult(payload);
      setCurrent((value) => {
        if (value.kind !== "filesystem.directory-page" && value.kind !== "filesystem.search-page") {
          return value;
        }
        return appendFilesystemPage(value, next);
      });
    } catch (reason) {
      setPagingError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoadingMore(false);
    }
  }, [current, loadingMore]);

  return (
    <FilesystemResultView
      result={current}
      {...(onReference ? { onReference } : {})}
      onLoadChildren={loadChildren}
      onLoadMore={(cursor) => void loadMore(cursor)}
      loadingMore={loadingMore}
      pagingError={pagingError}
    />
  );
}
