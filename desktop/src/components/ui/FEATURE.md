# UI primitives (`desktop/src/components/ui`)

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
