# FEATURE — Canonical media (every image & video in the app)

**The rule: no surface builds its own image UI, its own action, or its own
metadata dialog.** It builds a `MediaDescriptor` and hands it to the canonical
components. A user must never feel stuck because of *how* they reached an
image.

## The contract

`types.ts` — `MediaDescriptor` is the ONE shape. Everything an image can be
(library item, vault item, queue job, fresh generation result, a tool output, a
device capture) is described by it. Build one with a builder — never by hand:

| Builder | For |
|---|---|
| `descriptorFromLibraryItem(item, url, source)` | library + vault items |
| `descriptorFromJob(job, url)` | completed image-queue jobs |
| `descriptorFromVideoJob(job, url)` | completed video jobs |
| `descriptorFromResult(result, opts)` | a fresh one-shot generation |

`capabilitiesOf(descriptor)` decides which actions apply. A menu never shows an
action that cannot run (no dead clicks), and never hides one that can.

## The components

| Component | Use it for |
|---|---|
| `MediaThumb` | ANY image/video render. Click → lightbox, right-click → context menu, hover → info + "⋯". Variants: `icon`, `filmstrip`, `card`, `gallery`. |
| `MediaItemThumb` | The same, for engine-persisted items — resolves the auth'd blob URL from the ONE library/vault store (a plain `<img src>` cannot carry the Authorization header). |
| `MediaLightbox` | Full-screen viewer. Mounted **once**, by `MediaActionsProvider`. Surfaces call `actions.open(viewingSet, index)`. |
| `MediaInfoDialog` | The metadata view. Also exports `CopyButton` and `PromptBlock`. |
| `MediaContextMenu` / `MediaOverflowMenu` | Right-click and "⋯". Both render `buildMediaMenu()` — one list, so the abilities are identical. |
| `MediaActionsProvider` | Owns every action + the app-wide overlays. `useMediaActions()`. |

`onActivate` overrides what a plain click does (a filmstrip frame selects the
canvas image). Full size stays one step away via "⋯" or right-click — never
lost. `chrome="none"` hides the hover chrome; right-click still works.

## The action set

open · info · remix · use-as-input · reuse-seed · copy image · copy prompt ·
download · show in folder · move to Private · restore from Private · delete.

**Remix** reproduces *exactly* what made an image: model, prompt, negative
prompt, seed, size, steps, guidance, img2img strength, LoRAs, every advanced
pipeline kwarg — **and the input image it was generated from**. That last part
is why the engine stores the source image beside the result
(`<item_id>.init.png`; encrypted into the vault as `<item_id>.init.bin` when the
item is vaulted, so vaulting never destroys it). See `app/services/media_gen/
library.py` and `app/services/media_vault/service.py`.

## State: the rules that keep it coherent

**One store per thing.** `MediaLibraryProvider` and `MediaVaultProvider` mount
at the app root. Nothing calls `useMediaLibrary()` / `useMediaVault()` directly.
Three independent library stores is what let a deleted image stay on screen in
two other surfaces.

**Every removal is announced.** `lib/media-events.ts`:

- `announceMediaItemsRemoved(ids, reason)` — an item left the plaintext library
  (deleted, or vaulted). Every store holding that id drops it in the same tick:
  the library grid, the job thumbnails, the fresh-result pane, an open lightbox,
  the info dialog, the context menu.
- `announceMediaItemsAdded(ids)` — a vault restore put items back.
- `announceVaultLocked()` — **revoking a blob URL does not blank an `<img>` that
  already rendered.** A lock that only revokes URLs leaves decrypted vault
  images on screen for the rest of the session. Every store holding vault bytes
  must drop them when this fires.

**A byte-fetch that resolves after its item is gone must throw the URL away**
(epoch refs in `use-media-library.ts` / `use-media-vault.ts`) — otherwise it
caches a blob URL nothing will ever render or revoke, and for the vault it is a
decrypted blob surviving a lock.

## Prompts are never a dead end

Anywhere a prompt is shown truncated, it MUST be recoverable: a `CopyButton`
beside it, or a descriptor whose "⋯"/right-click reaches Info. A `title=`
tooltip is not an escape hatch (clips, unselectable, uncopyable).

The info dialog does **not** re-print the prompt inside the raw params JSON
(`extraParams()` strips the keys that have their own row) — the engine records
the prompt inside the pipeline kwargs, and dumping that through
`JSON.stringify(…, 2)` into a `<pre overflow-x-auto>` is what produced ten
page-widths of horizontal scroll. Every long value wraps
(`whitespace-pre-wrap break-words`); the dialog body is `overflow-x-hidden`.
