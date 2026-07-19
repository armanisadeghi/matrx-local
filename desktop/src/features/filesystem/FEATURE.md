# Desktop Filesystem UI

This directory is the canonical desktop presentation layer for local filesystem discovery. Do not add another chat-specific tree, Settings-only path model, or tool-output parser.

## Contracts

- `types.ts` owns the UI models: places, entries, directory pages, and result kinds. Paths are opaque strings resolved by the engine. React never constructs home paths, drive letters, or platform-specific standard folders.
- `tool-results.ts` normalizes structured tool output and durable `tool_call` / `tool_result` message blocks. Live tool events are reduced by `call_id`, so progress and results update one execution rather than creating duplicate cards.
- `use-filesystem-places.ts` combines `/system/paths` semantic aliases with `/filesystem/places`. Configured paths and priority roots appear alongside all other discovered accessible locations.
- `FilesystemResultView.tsx` is the reusable tree/list renderer. It supports nested/lazy children, cursor-ready pagination, breadcrumbs, selection, referencing, open, and reveal-in-parent actions.
- `FilesystemIndexSettings.tsx` is the Storage Settings surface for index status and priority roots. Priority changes reorder background work; they never exclude other folders or volumes.

## Result rendering

`components/tools/ToolExecutionCard.tsx` is the one execution/result card. Chat and the tool playground are adapters over it. It dispatches filesystem results here and image artifacts through the canonical media `MediaThumb`. New result kinds belong in that renderer registry, not in individual chat pages.

Filesystem tool results should use one of these envelopes:

```json
{
  "kind": "filesystem.directory-page",
  "namespace": "host",
  "path": "<opaque absolute path>",
  "entries": [],
  "next_cursor": null
}
```

```json
{
  "kind": "filesystem.places",
  "namespace": "host",
  "places": []
}
```

Keep the model-facing text concise and put renderable structure in result metadata or a structured output object.

## Cross-platform actions

- Directory selection uses `@tauri-apps/plugin-dialog` with `directory: true`.
- Open/reveal uses the allowlisted native Tauri `open_filesystem_path` command. Revealing a file opens its containing directory using separator-neutral path parsing; renderer code never chooses or interpolates an OS executable.
- Do not import macOS, Windows, or Linux path conventions into components. Platform-specific enhancements must be capability-gated and retain the portable action.

## Reuse points

- Chat: `ChatToolCall` → `ToolExecutionCard` → `FilesystemResultView`.
- Tool playground: `ToolOutput` → the same `ToolExecutionCard`.
- Settings: `FilesystemIndexSettings` and the shared engine API types.
- Files page: `pages/Files.tsx` → `FilesystemResultController` → the same `FilesystemResultView`.
- Future attachment picker, File Sync explorer, and storage chooser must compose these models/components rather than fork them.
