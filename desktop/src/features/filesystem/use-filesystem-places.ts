import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { engine, type EnginePaths, type FilesystemPlaceResponse } from "@/lib/api";
import { placesFromEnginePaths, type FilesystemPlace } from "./types";

let cachedPaths: EnginePaths | null = null;
let cachedPlaces: FilesystemPlace[] | null = null;
let cacheVersion = 0;
let inFlight: { version: number; promise: Promise<EnginePaths> } | null = null;
let placesInFlight: {
  version: number;
  promise: Promise<{ paths: EnginePaths; places: FilesystemPlace[] }>;
} | null = null;
let refreshInFlight: Promise<{ paths: EnginePaths; places: FilesystemPlace[] }> | null = null;

function invalidatePlacesCache(): number {
  cacheVersion += 1;
  cachedPaths = null;
  cachedPlaces = null;
  return cacheVersion;
}

async function fetchPaths(version: number): Promise<EnginePaths> {
  if (version === cacheVersion && cachedPaths) return cachedPaths;
  if (!inFlight || inFlight.version !== version) {
    inFlight = { version, promise: engine.getPaths() };
  }
  const request = inFlight;
  try {
    const paths = await request.promise;
    if (version === cacheVersion) cachedPaths = paths;
    return paths;
  } finally {
    if (inFlight === request) inFlight = null;
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

async function fetchPlaces(version: number): Promise<{ paths: EnginePaths; places: FilesystemPlace[] }> {
  if (version === cacheVersion && cachedPaths && cachedPlaces) {
    return { paths: cachedPaths, places: cachedPlaces };
  }
  if (!placesInFlight || placesInFlight.version !== version) {
    const promise = (async () => {
      const paths = await fetchPaths(version);
      let places: FilesystemPlace[];
      try {
        const discovered = await engine.getFilesystemPlaces();
        places = mergePlaces(paths, discovered.places);
      } catch {
        places = placesFromEnginePaths(paths);
      }
      if (version === cacheVersion) cachedPlaces = places;
      return { paths, places };
    })();
    placesInFlight = { version, promise };
  }
  const request = placesInFlight;
  try {
    return await request.promise;
  } finally {
    if (placesInFlight === request) placesInFlight = null;
  }
}

async function refreshPlaces(): Promise<{ paths: EnginePaths; places: FilesystemPlace[] }> {
  if (refreshInFlight) return refreshInFlight;
  const version = invalidatePlacesCache();
  const request = fetchPlaces(version);
  refreshInFlight = request;
  try {
    return await request;
  } finally {
    if (refreshInFlight === request) refreshInFlight = null;
  }
}

export function useFilesystemPlaces(enabled = true): [
  { places: FilesystemPlace[]; loading: boolean; error: string | null },
  { refresh: () => Promise<void> },
] {
  const [places, setPlaces] = useState<FilesystemPlace[]>(cachedPlaces ?? []);
  const [loading, setLoading] = useState(enabled && (!cachedPaths || !cachedPlaces));
  const [error, setError] = useState<string | null>(null);
  const requestId = useRef(0);
  const previousEnabled = useRef(enabled);

  const load = useCallback(async (force: boolean) => {
    const id = ++requestId.current;
    if (!enabled) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const next = force ? await refreshPlaces() : await fetchPlaces(cacheVersion);
      if (id !== requestId.current) return;
      setPlaces(next.places);
    } catch (reason) {
      if (id !== requestId.current) return;
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, [enabled]);

  const refresh = useCallback(async () => load(true), [load]);

  useEffect(() => {
    const reconnected = enabled && !previousEnabled.current;
    previousEnabled.current = enabled;
    if (enabled) void load(reconnected);
    else {
      requestId.current += 1;
      setLoading(false);
    }
    return () => {
      requestId.current += 1;
    };
  }, [enabled, load]);

  useEffect(() => {
    const onRootsChanged = () => {
      if (enabled) void refresh();
    };
    window.addEventListener("matrx-filesystem-roots-changed", onRootsChanged);
    return () => window.removeEventListener("matrx-filesystem-roots-changed", onRootsChanged);
  }, [enabled, refresh]);

  const state = useMemo(
    () => ({ places, loading, error }),
    [places, loading, error],
  );
  const actions = useMemo(() => ({ refresh }), [refresh]);
  return [state, actions];
}
