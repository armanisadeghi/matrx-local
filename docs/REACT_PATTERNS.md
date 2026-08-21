# React Patterns — the hook/effect rules that prevent polling outages

> Canonical body for the one-liners in [CLAUDE.md](../CLAUDE.md) § React rules.
> Every rule here maps to a shipped production bug — infinite API polling loops
> that flooded the engine. Read this before writing or reviewing any hook,
> effect, or provider in `desktop/src/`.

## `actions` objects must be stable

Every hook returning `[state, actions]` must wrap `actions` in `useMemo`:

```ts
// WRONG — new reference every render → infinite loops
const actions = { doThing, doOtherThing };

// CORRECT
const actions = useMemo(() => ({ doThing, doOtherThing }), [doThing, doOtherThing]);
```

## Never use `actions` as a useEffect dependency

```ts
// WRONG
useEffect(() => { actions.refresh(); }, [actions]);

// CORRECT — list the specific stable callback
useEffect(() => { refresh(); }, []);
```

## Init fetches belong in the hook, not the page

A page-level `useEffect([actions])` re-runs every render (state update →
re-render → new ref → loop). Put init fetches in `useEffect([])` inside the
hook.

## Persistent state belongs in Context, not page-level hooks

State surviving tab switches must live in a Context Provider at app level
(`App.tsx`). Pages call `useFooApp()` (context), not `useFoo()` (new instance).

Existing singletons: `LlmProvider`, `TtsProvider`, `TranscriptionProvider`,
`WakeWordProvider`, `TranscriptionSessionsProvider`, `PermissionsProvider`,
`AudioDevicesProvider`, `DownloadManagerProvider`.

## Polling intervals must be narrowly gated

Depend on the specific boolean being watched, not a broad object. Always
include cleanup.

```ts
// WRONG — restarts every render because of `actions` dep
useEffect(() => {
  if (state.status?.is_downloading) {
    const id = setInterval(() => actions.refreshStatus(), 2000);
    return () => clearInterval(id);
  }
}, [state.status?.is_downloading, actions]);

// CORRECT
useEffect(() => {
  if (!status?.is_downloading) return;
  const id = setInterval(() => void refreshStatus(), 2000);
  return () => clearInterval(id);
}, [status?.is_downloading, refreshStatus]);
```

## Focus/visibility handlers must be intentional

Only for re-fetching data changed externally (e.g., HF token set in browser).
Never re-initialize state or trigger full reloads on focus — this caused loops
in production.
