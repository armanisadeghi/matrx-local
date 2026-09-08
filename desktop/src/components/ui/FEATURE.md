# UI primitives (`desktop/src/components/ui`)

## The shared package owns every primitive in this folder

**There are no forks here any more.** Every shadcn primitive this app used to
carry a copy of lives in `@ai-matrx/design-system`; import it from there, never
from a local file. A new local definition of any of those names fails
`pnpm check:package-twins --strict` — all seventeen families are registered in
`scripts/package-twins.json`.

```tsx
import { Button, Badge, ScrollArea, Tooltip } from "@ai-matrx/design-system";
```

Two names do NOT map one-to-one, because the package ships two rungs of each:

| You want | Import |
|---|---|
| the flat control input (this app's historical field) | `BasicInput as Input` |
| the elevated, `shadow-textarea` field | `Input` |
| the flat control textarea (this app's historical field) | `BasicTextarea as Textarea` |
| the elevated textarea | `Textarea` |

### The four files that remain, and why

Each is a BINDING — one prop or one class deep, delegating to the package. None
re-implements behaviour, and each is allow-listed in `package-twins.json` with
that reason. Add nothing to them; if you need a capability they do not have, it
belongs in the package (THE SAME-SESSION LAW).

| File | Binds |
|---|---|
| `card.tsx` | `size="lg"` (this app's `p-6` density) + the glass surface |
| `dialog.tsx` | glass + `rounded-3xl` + `mobileSheet={false}` |
| `popover.tsx` | `w-auto min-w-[12rem] p-3` — popovers here size to their content; the package defaults to a fixed `w-72` |
| `select.tsx` | glass on the popup |

`number-input.tsx` is not a shim and not a fork: it is a host capability the
package does not ship (see **Number entry** below).

### Why the shims bind glass as utilities, not as `.glass`

`.glass` / `.glass-subtle` live in Tailwind's `components` layer; the package
paints `bg-card` / `bg-popover` / `shadow-md` as `utilities`, which come later —
so a shim that simply added `glass` would render an ordinary flat card and
nobody would see an error. The shims therefore bind the SAME `--glass-*` tokens
as utilities:

```
bg-[var(--glass-bg)] border-[color:var(--glass-border)] shadow-glass
```

`cn` is tailwind-merge, so those **substitute** the package's classes, and a
caller's own `border-destructive/50` / `shadow-xl` then substitutes for the
glass in turn — which is the order that has to hold: package loses to host,
host loses to the call site. Six call sites depend on that second half (the
Settings and Devices danger cards, the setup wizard, the login card, the
notification tray, the Ports dialog).

For it to work, `cn` in `lib/utils.ts` registers `shadow-glass` in
tailwind-merge's `shadow` group — the library cannot know about this app's
`boxShadow.glass` theme entry and otherwise files it under shadow-COLOR, where
it neither replaces nor is replaced by anything. Note also that
`shadow-[var(--glass-shadow)]` is NOT a substitute: Tailwind compiles a bare
`var()` there to a shadow *colour*, and tailwind-merge dedupes no var-valued
shadow at all. Both were caught on the built stylesheet, not in review.

### The status colours are a host mapping, and they are load-bearing

`tailwind.config.ts` maps `success` / `warning` / `info` onto the package's
tokens. Without those three entries the package's `Badge` `success`/`warning`/
`info`, `Button variant="success"`, `Progress tone=` and `Alert` variants
generate **no utility at all** and render as unstyled text — silently
(design-system 0.4.0 Consumer action 2b). Never delete them; the VALUES come
from the package's `tokens.css` defaults unless `index.css` overrides them.

### `tailwindcss-animate` stays in this repo

design-system 0.10.0 told matrx-extend to delete the plugin. **That does not
apply here:** seven components in this app use `animate-in` / `slide-in-from-*`
of their own, and the plugin is genuinely loaded (a v3 plugin in a v3 app). The
package's own motion is `matrx-`-prefixed and ships in its `styles.css`.

### History

Adopted 2026-09-07 (census row 19). Until that day this repo had **never** been
put on `@ai-matrx/design-system` — the campaign's census row said "all four
repos" while five consumers exist, and design-system 0.7.0's "no other consumer
repo carries a fork of these" had swept only the other four. Both records are
corrected in place. Seventeen forks were swapped, fourteen files deleted, 131
files repointed, re-grep zero.

Deliberate visual convergences from that swap, so nobody files them as
regressions: `Badge` `default` is the soft `bg-primary/15` tint rather than a
solid primary chip and its `success`/`warning` are tokens rather than raw
emerald/amber; `Tooltip` is a bordered popover surface rather than a solid blue
chip; the unchecked `Checkbox` border and the `Slider` track/thumb take the
package's geometry. Retiming any package motion is a `--matrx-motion-*` token
change, never a rule override.

## Number entry

**Always use `NumberInput` from `@/components/ui/number-input` for numeric fields.**

Never bind a controlled input to a bare `number` and coerce on every keystroke:

```ts
// FORBIDDEN — clearing the field snaps back to 0 / previous / min
value={count}
onChange={(e) => setCount(Number(e.target.value) || 1)}
```

`NumberInput` keeps a string draft while focused so the field can be blank, then
commits/clamps on blur. Live `onChange` fires only when the draft parses to a
finite number.

## Floating overlays

Account menus, notifications, selects, and similar floating controls use the
shared `Popover` / `Select` primitives instead of hand-rolled absolute
positioning and document click listeners. Their portal content sits at the
package's `z-[10000]` (Popover, Dialog) / `z-[10001]` (Select, Tooltip) rungs,
above the desktop shell and quick-action bar — the fork's `z-[100]` is gone.
User-facing popovers and notification toasts render on an opaque `bg-popover`
surface so content behind them cannot bleed through.
