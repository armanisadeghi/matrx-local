import type { EnginePaths } from "@/lib/api";

export type FilesystemEntryKind = "directory" | "file" | "symlink" | "other";
export type FilesystemNamespace = "host" | "workspace" | "managed-files" | "notes" | "unknown";
export type FilesystemModifiedAt = number | string;

export interface FilesystemEntry {
  name: string;
  path: string;
  kind: FilesystemEntryKind;
  size?: number | null;
  modifiedAt?: FilesystemModifiedAt | null;
  hidden?: boolean;
  hasChildren?: boolean;
  children?: FilesystemEntry[];
}

export interface FilesystemDirectoryPage {
  kind: "filesystem.directory-page";
  namespace: FilesystemNamespace;
  path: string;
  entries: FilesystemEntry[];
  summary?: string;
  nextCursor?: string | null;
  total?: number | null;
  source?: "index" | "disk" | "hybrid";
}

export interface FilesystemSearchPage {
  kind: "filesystem.search-page";
  namespace: FilesystemNamespace;
  query: string;
  root?: string | null;
  entries: FilesystemEntry[];
  summary?: string;
  nextCursor?: string | null;
  source?: "index" | "disk" | "hybrid";
  indexComplete?: boolean;
}

export interface FilesystemContentMatch {
  path: string;
  snippet: string;
}

export interface FilesystemContentSearch {
  kind: "filesystem.content-search";
  namespace: FilesystemNamespace;
  query: string;
  results: FilesystemContentMatch[];
  summary?: string;
}

export interface FilesystemSemanticMatch {
  score: number;
  entry: FilesystemEntry;
}

export interface FilesystemSemanticSearch {
  kind: "filesystem.semantic-search";
  namespace: FilesystemNamespace;
  query: string;
  model: string;
  results: FilesystemSemanticMatch[];
  summary?: string;
}

export interface FilesystemPlace {
  id: string;
  label: string;
  path: string;
  alias?: string;
  category?: "home" | "standard" | "configured" | "volume";
  priority?: number;
  available?: boolean;
  configured?: boolean;
}

export interface FilesystemPlacesResult {
  kind: "filesystem.places";
  namespace: FilesystemNamespace;
  places: FilesystemPlace[];
  summary?: string;
}

export type FilesystemResult =
  | FilesystemDirectoryPage
  | FilesystemSearchPage
  | FilesystemContentSearch
  | FilesystemSemanticSearch
  | FilesystemPlacesResult;

const PLACE_ALIASES: ReadonlyArray<[string, string]> = [
  ["@home", "Home"],
  ["@user", "Matrx"],
  ["@files", "Files"],
  ["@code", "Code"],
  ["@workspaces", "Workspaces"],
  ["@notes", "Notes"],
];

/** Build user-facing places exclusively from engine-resolved paths. */
export function placesFromEnginePaths(paths: EnginePaths): FilesystemPlace[] {
  const seen = new Set<string>();
  const places: FilesystemPlace[] = [];

  for (const [alias, label] of PLACE_ALIASES) {
    const path = paths.aliases[alias];
    if (!path || seen.has(path)) continue;
    seen.add(path);
    places.push({ id: alias.slice(1), label, path, alias });
  }

  const screenshots = paths.resolved.screenshots;
  if (screenshots && !seen.has(screenshots)) {
    places.push({
      id: "screenshots",
      label: "Screenshots",
      path: screenshots,
    });
  }
  return places;
}
