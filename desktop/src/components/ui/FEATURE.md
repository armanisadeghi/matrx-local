# UI primitives (`desktop/src/components/ui`)

## Shared package boundary

`Separator` is a compatibility export over exact public
`@ai-matrx/design-system@0.1.1`. Tailwind scans the installed package artifact,
so package-owned utility classes cannot disappear from production CSS. Button,
Badge, and Label remain local because their current styling and element contracts
are not yet identical to the shared primitives; a matching filename alone is not
evidence for extraction.

### The un-collapsed fork census (recorded 2026-09-07, C28 catch-up)

`Separator` is still the ONLY shim in this folder. Seventeen files here are
local implementations of components `@ai-matrx/design-system` has since taken
ownership of:

- **0.5.0 wave** — `avatar`, `card`, `checkbox`, `dialog`, `progress`,
  `scroll-area`, `switch`, `tabs`, `textarea`
- **0.7.0 wave** — `tooltip`, `slider`, `popover`, `select`
- **older waves** — `button`, `badge`, `label`, `input`

This matters beyond tidiness: both of those CHANGELOG entries name their
holders explicitly, and **neither one names this repo** — 0.7.0 states "No
other consumer repo carries a fork of these — verified by census on 2026-09-07
across matrx-extend, matrx-games, aidream `apps/dashboard` and
`apps/workflow-studio`". `matrx-local` was not in that census, so the campaign's
own record of where the twins live is incomplete, and every future wave that
reads it will keep skipping this app. The gap is reported upward; do not treat
the package CHANGELOGs as a complete census of this repo.

Collapsing them is NOT a drive-by: it is Arman's C20 supervised guided session
(the desktop app's whole visual language rides on these files), and this repo's
release stays blocked on that session. Until then:

- **Never add an eighteenth fork.** `scripts/package-twins.json` now registers
  the Accordion and Collapsible families — the two the package owns that this
  repo has never defined — so a new hand-rolled one fails
  `pnpm check:package-twins --strict` instead of shipping.
- The families listed above are deliberately absent from that register: this
  repo defines them today, so a row would assert a collapse that has not
  happened. Add each row the same session its fork is deleted.
- The CSS contract IS current: `src/index.css` declares the layer order and
  imports the package's `tokens.css` + `styles.css`, so the package-owned
  structural rules (`.matrx-accordion-content`, `.matrx-collapsible-content`,
  glass/scroll-fade/safe-area chrome) are present the moment a package
  component is adopted.

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
shared Radix `Popover` / `Select` primitives instead of hand-rolled absolute
positioning and document click listeners. Their portal content sits at
`z-[100]`, above the desktop shell and quick-action bar. User-facing popovers
and notification toasts render on an opaque `bg-popover` surface so content
behind them cannot bleed through.
