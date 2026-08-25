# UI primitives (`desktop/src/components/ui`)

## Shared package boundary

`Separator` is a compatibility export over exact public
`@ai-matrx/design-system@0.1.1`. Tailwind scans the installed package artifact,
so package-owned utility classes cannot disappear from production CSS. Button,
Badge, and Label remain local because their current styling and element contracts
are not yet identical to the shared primitives; a matching filename alone is not
evidence for extraction.

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
