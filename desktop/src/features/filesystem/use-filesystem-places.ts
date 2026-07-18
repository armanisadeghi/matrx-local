import { useCallback, useEffect, useMemo, useState } from "react";
import { engine, type EnginePaths, type FilesystemPlaceResponse } from "@/lib/api";
import { placesFromEnginePaths, type FilesystemPlace } from "./types";

let cachedPaths: EnginePaths | null = null;
let cachedPlaces: FilesystemPlace[] | null = null;
let inFlight: Promise<EnginePaths> | null = null;

async function fetchPaths(): Promise<EnginePaths> {
  if (cachedPaths) return cachedPaths;
  inFlight ??= engine.getPaths();
  try {
    cachedPaths = await inFlight;
    return cachedPaths;
  } finally {
    inFlight = null;
  }
}

function mergePlaces(paths: EnginePaths, discovered: FilesystemPlaceResponse[]): FilesystemPlace[] {
  const base = placesFromEnginePaths(paths);
  const byPath = new Map(base.map((place) => [place.path, place]));
  for (const place of discovered) {
    byPath.set(place.path, { ...byPath.get(place.path), ...place });
  }
  return [...byPath.values()].sort((left, right) => (right.priority ?? 0) - (left.priority ?? 0));
}

async function fetchPlaces(): Promise<{ paths: EnginePaths; places: FilesystemPlace[] }> {
  const paths = await fetchPaths();
  try {
    const discovered = await engine.getFilesystemPlaces();
    cachedPlaces = mergePlaces(paths, discovered.places);
  } catch {
    cachedPlaces = placesFromEnginePaths(paths);
  }
  return { paths, places: cachedPlaces };
}

export function useFilesystemPlaces(): [
  { places: FilesystemPlace[]; loading: boolean; error: string | null },
  { refresh: () => Promise<void> },
] {
  const [paths, setPaths] = useState<EnginePaths | null>(cachedPaths);
  const [places, setPlaces] = useState<FilesystemPlace[]>(cachedPlaces ?? []);
  const [loading, setLoading] = useState(!cachedPaths || !cachedPlaces);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    cachedPaths = null;
    cachedPlaces = null;
    try {
      const next = await fetchPlaces();
      setPaths(next.paths);
      setPlaces(next.places);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (paths && cachedPlaces) return;
    void fetchPlaces()
      .then((value) => {
        if (!cancelled) {
          setPaths(value.paths);
          setPlaces(value.places);
        }
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [paths]);

  useEffect(() => {
    const onRootsChanged = () => void refresh();
    window.addEventListener("matrx-filesystem-roots-changed", onRootsChanged);
    return () => window.removeEventListener("matrx-filesystem-roots-changed", onRootsChanged);
  }, [refresh]);

  const state = useMemo(
    () => ({ places, loading, error }),
    [places, loading, error],
  );
  const actions = useMemo(() => ({ refresh }), [refresh]);
  return [state, actions];
}
