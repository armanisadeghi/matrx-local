import type { EnginePaths } from "@/lib/api";

export type FilesystemEntryKind = "directory" | "file" | "symlink" | "other";

export interface FilesystemEntry {
  name: string;
  path: string;
  kind: FilesystemEntryKind;
  size?: number | null;
  modifiedAt?: string | null;
  hidden?: boolean;
  hasChildren?: boolean;
  children?: FilesystemEntry[];
}

export interface FilesystemDirectoryPage {
  kind: "filesystem.directory-page";
  namespace: "host" | "workspace" | "managed-files" | "notes" | "unknown";
  path: string;
  entries: FilesystemEntry[];
  summary?: string;
  nextCursor?: string | null;
  total?: number | null;
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
  places: FilesystemPlace[];
  summary?: string;
}

export type FilesystemResult = FilesystemDirectoryPage | FilesystemPlacesResult;

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
