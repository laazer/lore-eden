# lore-eden (ts)

The UI kit. Today: design tokens and theming, and a pane/canvas layout system.

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


## Arrangements

Two ways to place panes, sharing one pane implementation.

**A split grid** — nested resizable splits, where a pane's size is a grow factor
rather than a stored pixel count. **A free canvas** — items placed anywhere, with
pan, zoom, and a z-order.

```tsx
import { FlexGridSurface, CanvasSurface, readGridTree } from '@lore-eden/ui';

<FlexGridSurface
  tree={readGridTree(layout)}
  containers={layout.containers}
  edit={queue.edit}
  pickPrimitive={pick}
/>
```

Both are **controlled**: they take the layout and hand back edits. They do no
routing, hold no cache and know nothing about your backend — the versions this
came from read the view id from the router and their writes from a data-layer
hook, which is what made them impossible to reuse.

### Panes

A pane's contents are a *primitive*: a component registered against an id, which
a container stores. Arrangement code never names one, so adding a pane type is a
registration and no change to either surface.

```tsx
const notes = definePrimitive({
  id: 'notes',
  displayName: 'Notes',
  containerKind: 'panel',
  settingsFields: [{ kind: 'string', key: 'title', label: 'Title', default: 'Untitled' }],
  parseSettings: (s) => ({ title: String(s.title ?? 'Untitled') }),
  Component: ({ settings }) => <Notes {...settings} />,
});

new PrimitiveRegistry().register(notes);
```

Ids are resolved through a `Map`, never a plain-object lookup — a stored id is
text a user influenced, and `settings['__proto__']` on an object reaches
`Object.prototype`. Registering one id twice is refused rather than silently
replacing the first.

### Sizing by tier

A primitive is told how much room it has, as a **tier** rather than a pixel
count:

```tsx
const { tier, width, height } = usePaneSize();  // 'compact' | 'regular' | 'wide'
```

Exact numbers are published too — a chart or a virtualiser needs them — but the
layout decision is a tier, so the thresholds are chosen once instead of being
scattered as magic numbers through every pane.

Height counts. A pane 900px wide and 90px tall is `compact`: the constraint felt
there is vertical, and a primitive consulting only width would lay a comfortable
three-column card into a letterbox.

Before the first measurement the tier is `regular`, not `compact` — guessing
compact flashes every pane's dense layout on mount, and `regular` is the layout
most primitives are already written for, so the first frame looks like the steady
state. Where `ResizeObserver` does not exist the tier simply stays `regular`.

The tier is also a `data-pane-tier` attribute, so a pane can answer the easy
cases in its own stylesheet without re-rendering on every drag frame.

### Persisting

The surfaces do not persist; the host does. What the host should not have to
re-derive is the *ordering*, so that is here:

```ts
const queue = createLayoutQueue({
  read: () => cache.current,
  write: (layout) => api.save(layout),
  onWritten: (stored) => { cache.current = stored; cancelInFlightReads(); },
});
```

Four properties, each of which was a bug first:

- **The base is the newest layout, not one captured at click time.** The edit is
  a function called when the queue reaches it. A body composed at pointer-down
  reverts whatever the in-flight write was saving, and a backend that replaces
  the layout wholesale accepts it happily.
- **Writes to one layout are serialized.** Two edits against the same base both
  save, and the second discards the first.
- **A write carries its own identity.** One queue per layout, so a write issued
  against one view that lands after you opened another cannot write into it.
- **A stale read must not land on top of a write.** The queue cannot cancel the
  host's reads, so it announces each landed write through `onWritten` — cancel
  there. The revert is otherwise invisible: every layout involved is valid, and
  it is the *next* edit, composing from the reverted cache, that does the damage.

Pan and zoom are a separate path on purpose: they fire continuously while a
content edit may be in flight, and queueing them behind it makes the canvas feel
stuck.

### Slots

Three things the host supplies, because they are its decisions and not this
package's: the primitive **picker**, a pane's **settings editor**, and which
primitives the **registry** offers. Omit the settings slot and the settings
control does not appear — a button that opens nothing is a control that lies.
