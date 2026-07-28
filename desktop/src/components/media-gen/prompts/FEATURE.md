# Media-generation prompt editors

All user-authored media prompts and list-content editors use
`ResizablePromptTextarea`. Do not add native `resize-y` prompt textareas or
surface-specific sizing logic.

## Sizing contract

- Primary prompt editors open at 10 rows.
- Negative prompt editors open at 6 rows.
- Every editor can shrink to 5 rows and grow to 1200 px.
- A visible, full-width grip replaces the browser's tiny corner handle.
- The grip supports pointer dragging, Arrow Up/Down keyboard resizing, Home to
  reset, and double-click to reset.
- Height is persisted in localStorage by a stable, per-surface key from
  `PROMPT_TEXTAREA_KEYS`. Image and video surfaces must not share keys.

`VariablePromptInput` and `VariablePromptTextarea` compose the same canonical
component so list-token insertion never creates a separate editor variant.
`PromptMatrix/TemplateEditor` uses `usePersistentTextareaResize` and
`TextareaResizeHandle` directly because its token-highlight mirror must occupy
the exact same height as its native textarea.

JSON/code editors are not prompt-content editors and retain their own sizing
behavior.
