# lore-eden (ts)

The UI kit. Today: design tokens and theming.

```bash
cd ts && npm install
```

## Tokens

Every token is defined once, as an entry carrying both its CSS custom-property
name and its value. The views callers use are derived from that entry:

```ts
import { tokens, vars, cssProperties, cssVar } from '@lore-eden/ui';

tokens.accent      // "#34d77f"          raw value
vars.accent        // "var(--accent)"    reference — follows the active theme
cssProperties      // { "--accent": … }  for injecting into a rule
cssVar('accent')   // "var(--accent)"    reference, for a token chosen at runtime
```

Prefer a **reference** when styling a component. A raw value is frozen at the
moment it was read; a reference resolves wherever it is used, so it follows the
theme without the component knowing there is one.

Category groupings (`colors`, `typography`, `spacing`, `radius`, `shadows`,
`motion`) carry values and are derived from the same table.

### Why one table

The source kept four hand-written lists that had to agree — the category
objects, the `var()` map, the injectable properties, and a `cssVar()` helper
that recomputed the name — and they had already drifted:

- `cssVar()` returned a different CSS name from the `cssVars` map for **9 of 54
  tokens**, every spacing stop among them, plus its own documented example.
  `cssVar('sp4')` produced `var(--sp4)`, which nothing defines, so any
  declaration using it was silently dropped.
- The theme object's colour list had lost `chrome2`, which existed everywhere
  else.

CSS names and values are carried over unchanged — they are the contract with any
stylesheet that references them. Only the duplication went.

## Theming

```tsx
import { ThemeProvider, useTheme } from '@lore-eden/ui';

<ThemeProvider mode="light">
  <App />
</ThemeProvider>
```

The provider does two things: injects the tokens as custom properties (a `:root`
block and a `:root[data-theme="light"]` block), and publishes the mode on a
context.

**Dark is the default.** Light is a sparse set of overrides on top of it, so a
token added to the table is inherited by light automatically rather than
silently going missing.

```tsx
const theme = useTheme();
theme.mode          // "light"
theme.values.bg     // the light value — resolved for the active mode
theme.vars.bg       // "var(--bg)"
theme.spacing(2)    // "8px"
theme.breakpoints.down('md')  // "@media (max-width: 959px)"
```

`useTheme()` follows the mode. In the source it was a singleton built from the
dark values that never learned the mode existed, so anything styled from a raw
theme value stayed dark in light mode while `var()` references switched — half a
component would change and half would not, which reads as a styling mistake
rather than a missing wire.

Outside a provider the hook returns the dark theme rather than throwing, so a
component rendered alone in a test or a story still has values.

### Scoped accent

```tsx
<ThemeProvider accentOverride="#ff0099">
  <Canvas />
</ThemeProvider>
```

Applies to a wrapper element, not `:root`, so one region carries its own accent
without disturbing the page.

## Tests

```bash
cd ts && npm test
```

They assert on the **resolved** custom properties in a real DOM, not on the
stylesheet string the provider injected — a stylesheet that is present but not
applying is exactly the failure a string assertion misses.
