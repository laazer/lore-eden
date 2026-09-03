# lore-eden (ts)

The UI kit: design tokens and theming, responsive breakpoints and window layout,
a pane/canvas layout system, and chat.

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


## Chat

A transcript and a composer, with three seams where the source had one product
hardcoded.

```tsx
<ChatMessages
  messages={messages}
  isThinking={session.isBusy}
  activeTurnId={session.activeTurnId}
  partRegistry={parts}
  renderAvatar={({ activity }) => <MyAvatar state={activity} />}
/>
<ChatComposer value={draft} onChange={setDraft} onSubmit={send} onStop={stop} />
```

### Streaming is a transport

Reasoning arrives while a turn runs, and every host does it differently — a
websocket, SSE, a poll, a generator in a test. Supply one:

```tsx
<ThinkingTransportContext.Provider value={{ subscribe: (turnId, onFrame) => … }}>
```

Frames are **whole transcripts, not deltas**, which is what lets a transport mix
a socket with a catch-up poll without the two agreeing on ordering. With no
transport the transcript renders settled messages and no live stream — a working
chat, not a broken one.

### Parts are a registry

A message can carry structured content beyond its text. The source had twenty
such parts, each a card for one of its own nouns; none of that travels, because
the parts *are* the product.

```tsx
new ChatPartRegistry()
  .register('diff', DiffCard)
  .setFallback(UnknownPartCard);
```

An unknown kind falls back rather than throwing: a transcript outlives the build
that wrote it, and a message naming a part this build dropped must still be
readable. Set a fallback — rendering nothing makes a message look like it said
less than it did.

One part shape ships: `{ kind: 'text', content }`. A transcript has to know what
a message *says* to preview it or fall back to it.

### The avatar is a slot

`renderAvatar` is told what the assistant is doing (`idle | thinking |
answering`). Omit it and the transcript is text, which reads perfectly well.

### The composer

Fully controlled — it holds no draft. `/` and `@` menus are driven through a
`ComposerCommands` binding carrying only what the menu needs; the source's
carried twenty-odd members, most of them one app's composer features. Those go
above the input through `renderAbove` instead.

The command path is consulted **before** the send gate, deliberately: a command
like "stop" has to work at exactly the moment an ordinary send is refused.


## Responding to the window

Two sizing systems ship, and they answer different questions. Picking the wrong
one gives you a component that ignores the constraint actually applying to it.

| | Measures | Stops | Use it for |
|---|---|---|---|
| `useScreenSize()` | the **window** | 6 (`xs`…`x2`) | page-level layout — chrome, columns, whether a sidebar exists |
| `usePaneSize()` | a **container** | 3 (`compact`/`regular`/`wide`) | a pane's own contents |

A pane 300px wide in a 4K window is cramped whatever the breakpoint says — only
the container measurement knows that. Equally, an app bar's height is a property
of the window, not of any one pane. Both are right; they are not alternatives.

### Per-size values

Any value can be given per breakpoint:

```tsx
const width = useFlexValue({ xs: '100%', md: '50%', xl: 320 });
```

Resolution searches **outward** from the active size — nearest defined in either
direction, narrower preferred at a tie — then `base`, then your fallback. Most
systems only cascade down, which leaves a value set for `xl` simply absent at
`xs`; searching outward means a partial config always resolves to something its
author actually wrote.

`base` is not a breakpoint. It is the value that applies at every size, and it
sits outside the size map so the outward search cannot reach it as a neighbour.

### Measured once per boundary, not per render

`useScreenSize` reads `window.innerWidth` when a resize **crosses a breakpoint**,
not on every render and not on every resize event. Reading layout in a render
path forces the browser to flush pending style work to answer it, and dragging a
window edge fires hundreds of events that change nobody's answer.

`screenSizeNow()` exists for imperative callers outside React, named so the live
read is obvious.

## Window layout

The regions an application reserves, and what stacks above what.

```tsx
const bar = useRegion('appBar');
<header style={regionStyle(bar)}>…</header>
```

A region is **named**, so a component asks where it belongs rather than deriving
a position, and its size can differ per breakpoint — the right-hand toolbar takes
33% of a narrow window and 35% of a wide one without anyone writing a media
query.

### The z-index ladder

```ts
LAYERS.notification  // 20000
LAYERS.appBar        // 18000
LAYERS.toolbar       // 14000
LAYERS.body          // 1
```

A named rung instead of a number at the call site. The alternative is what every
codebase without one accumulates: `z-index: 9999` beside `z-index: 10000` beside
`z-index: 999999`, each written by someone who could not see the others. The
thousand-wide gaps are deliberate — room to slot something in without
renumbering, and renumbering is the change that quietly reorders things nobody
was watching.

**This is not the canvas's z-order.** `layout/canvas` has a *free* stack a user
drags and reorders. This ladder is *fixed* application chrome that nothing
reorders at runtime.

**And "flex" here is not `FlexGridSurface`.** That surface's flex is the CSS grow
factor in a split grid; the flex here is breakpoints.

### Hidden is not zero-sized

A hidden region renders `display: none`; a zero-height one still holds an empty
box open. Collapsing the two leaves a gap with no visible cause.

## CSS values as data

`style/` turns the strings you would otherwise concatenate into values you can
compute with: lengths that know their unit, rectangles, structured border props,
anchoring, and colour.

```ts
import { styleUnit, makeBorderStyles, EasyColor, marginFromAnchor } from '@lore-eden/ui';

styleUnit('10%').mult(3).asString;              // '30%'
styleUnit('10px').plusU(styleUnit('5%'));       // throws UnitMismatchError
makeBorderStyles({ style: 'solid', weight: 2, topLeft: { radius: 4 } });
// { borderStyle: 'solid', borderWidth: '2px', borderTopLeftRadius: '4px' }
EasyColor.contrastingHex('#1e1e1e');            // '#ffffff'
marginFromAnchor('left');                       // left, vertically centred
```

### The unit check is the point

Adding a `%` to a `px` is a question with no answer. `plusU`/`minusU` throw
`UnitMismatchError` rather than pick one, because a layout that silently picks
one is wrong in a way nobody can see.

Scaling by a plain number — `mult`, `div` — always means something, so those
take numbers. There is no `multU`/`divU`: 100px × 2px is 200px², and a method
that returns it labelled `px` is lying.

### Parsing is a regex, not a search for unit names

Searching a string for `"em"` finds it inside `"2rem"`. That is how the source
parsed `2rem` as `NaN`, and why `rem` and `vw` could not be added to the list
without breaking `em` and `vh`. One regex has neither problem.

### Absent and unparseable are different

`unitNumber()` is `0`; `unitNumber('nonsense')` is `NaN`. "Nobody set this" and
"somebody set this to nonsense" want different reactions from the caller.

### Colour wraps tinycolor2, immutably

`EasyColor` is a thin wrapper over `tinycolor2` — ~15KB that already knows
every CSS colour format. Writing the conversions by hand instead landed two
silent off-by-ones against it, one of which turns on `Math.round(-25.5)` being
`-25` rather than `-26`. That is what a colour library is for.

`brighter` adds to each RGB channel and `darken` reduces HSL lightness — the
two are not inverses. The asymmetry is the library's, and it is what the
source's palette was tuned against.

The wrapper adds two things. It is **immutable**: `tinycolor2`'s `brighten` and
`darken` mutate the receiver and return it, so under a fluent API two callers
holding one colour change it under each other, and `darkenHex` leaves its input
darkened. And a colour it cannot parse is an **error**, not black — the library
answers an invalid colour with a valid-looking black, so a mistyped colour name
renders as though somebody chose it.

### Border properties come from a table

`borderTopLeftRadius` and `border-left-width` are spelled out, per side and per
corner, rather than assembled from fragments. An assembled name can be one no
browser knows — and React drops an unknown style property without a word, so
the failure is a border that quietly does not appear.

## Primitives

Pieces with no dependency on the rest of the kit, extracted because nothing in a
standard library or a common package does quite what they do.

### Observable — change channels, not events

A subscriber names the *kinds* of change it cares about and is called **once**
per emission, however many of those kinds the emission touched:

```ts
const off = doc.observeChangeTypes(render, ['title', 'body']);
```

`render` runs once when a change reports both. An emitter that fanned out per
channel would call it twice and leave the caller to dedupe — which is the work
this does. `'any'` hears everything, and subscribing returns its own
unsubscribe, so an inline arrow can be removed later.

`ObservableDict` makes each key its own channel, so a component can watch one
field without waking on every other write.

### The checkpoint stack is not browser history

Browser history is where you *came from*. A user who wandered through six
settings screens wants one gesture back to the document they were editing, not
six presses. Checkpoints are pushed deliberately, and `jump` skips any that
resolve to where you already are — two paths can be the same place (`/` and
`/home`), and returning to one you are standing on reads as a broken button.

It takes a `NavAdapter` — `{ currentPath, navigate }` — rather than calling a
router. The source called react-router v5's `useHistory`, removed in v6; a
shared library cannot pin its consumers to a router, let alone a superseded
major of one.

```tsx
const adapter = { currentPath: useLocation().pathname, navigate: useNavigate() };
<CheckpointProvider adapter={adapter}>…</CheckpointProvider>
```

### LruOrderedSetCache remembers two orders

Insertion order is what you read back; recency decides who gets evicted. A plain
LRU gives you eviction but shuffles the display, and an insertion-ordered set
gives you a stable display and no eviction policy. Open tabs and recent files
want both.

### Queried is a union, not a bag of booleans

After `if (result.isSuccess)` the data exists; before it there is nothing to
read. No optional chaining, no non-null assertion, no branch you forgot.
`fromQueryLike` adapts react-query, TanStack Query, SWR or a hand-rolled fetch
hook, because it reads only the flags they all agree on — nothing here imports a
query library.

### TabView keeps your place

The reconciliation is the point. Tabs are identified by key, so closing an
earlier tab does not slide the selection onto a different document, and adding
one selects it. `reconcileSelection` is exported and tested without a DOM.

### Hooks

`usePrevious`, `useEventListener`, `useThrottle`, `useMousePosition`.

`useEventListener` keeps the handler in a ref, so passing an inline arrow — what
callers actually do — does not detach and reattach every render. `useThrottle`
is leading-edge and cancels on unmount. `useMousePosition` coalesces to one
update per animation frame; a mouse fires `mousemove` faster than React can
render, and a frame is the finest granularity anything visual can use.

### Errors

`describeError(value)` narrows an unknown thrown value to a message, and
`asError(value)` to an `Error`. One helper rather than an `instanceof Error`
ternary at each call site, each copy free to disagree about what a thrown
non-`Error` should say. The organization gate enforces it.

## Controls

Buttons, inputs, a select, a switch, a checkbox, a field wrapper, tags, keycaps,
status dots, skeletons, a spinner and a toast.

```tsx
import { Field, TextInput, Button, Toast } from '@lore-eden/ui';

<Field label="Email" error={problem}>
  <TextInput />
</Field>
<Button variant="primary" onClick={save}>Save</Button>
<Toast message="Saved" tone="ok" duration={4000} onDismiss={hide} />
```

### The source had no styling

This is the one extraction where the plan's premise turned out to be wrong. The
components were surveyed as "real, tested, Storybook-documented", and their
behaviour is — but they emit BEM-ish class names and **thirty of the thirty-four
have no definition in any stylesheet in that application.** They render as
unstyled browser defaults.

Their tests pass because they assert the class *string*:

```tsx
expect(btn).toHaveClass('btn--primary');   // a class nothing defines
```

So the behaviour came across and `controls.css` is new, written against the
tokens in `tokens/` — which is what makes these follow the theme without any
component reading a value. The tests here assert behaviour and accessibility;
none of them asserts a class name.

### What the components actually guarantee

- **`Button` defaults its type to `button`.** HTML defaults it to `submit`, so a
  button inside a form that omits it submits the form — a bug that presents as a
  routing problem.
- **`Field` wires its three parts together.** The control gets an id, the label
  points at it, and the hint is referenced by `aria-describedby`, so the guidance
  is read as part of the field. The source rendered the same three elements with
  none of those attributes.
- **`Switch` is `role="switch"`**, so a screen reader says "on/off" rather than
  "checked".
- **`Checkbox` links its label without needing an id**, by wrapping. The source
  required one and silently produced an unlinked label without it.
- **`StatusDot` is decoration unless given a label.** A bare coloured dot says
  nothing to anything that does not render colour.
- **`Toast` interrupts only for a problem** — `role="alert"` for warnings and
  failures, `role="status"` otherwise — and its timer does not restart when its
  parent re-renders.
- **Focus is restyled, never removed.** `outline: none` with no replacement is
  the commonest way a component library becomes unusable by keyboard.
- **Everything forwards a ref**, including `Checkbox`, which keeps one of its own
  for the indeterminate write.

## Long-lived sockets

`chat/thinking.ts` defines a `ThinkingTransport` and ships nothing behind it,
which is right — a websocket URL belongs to a host. But every host filling that
seam needs reconnection with backoff and would write it, so `sockets/` offers
one. The chat components do not import it; a host with its own transport never
touches it.

```tsx
<ThinkingTransportContext.Provider
  value={createWebSocketThinkingTransport({
    url: (turnId) => `wss://host/turns/${turnId}/thinking`,
  })}
>
```

`ReconnectingSocket` owns only the lifecycle — opening, backing off, and the
difference between a drop and a close we asked for. Framing and meaning stay
with the caller, because those are the parts that actually differ between a
queue feed and a chat turn, and folding them in would trade one duplication for
a switch statement. `JsonSocket` is the no-subclass version.

Three details worth keeping:

- **Three states, not four.** An earlier client had an `error` that behaved
  identically to `closed` for every consumer, and an `error` that never cleared
  was how a dashboard got stuck.
- **`closed`, not `connecting`, through the backoff.** A caller showing a
  spinner through the wait shows nothing useful, and one that can fall back to
  polling must be told to start now rather than after the retry also fails.
- **A close we asked for is not reported as a drop.** Handlers are dropped
  before `close()`, or the caller reconnects — or starts polling — on its way
  out of the page.

The backoff resets when a connection *opens*, not when data arrives: a socket
nobody talks on would otherwise creep to the ceiling across quiet reconnects.
