# When a UI change is done

Passing the type checker and the unit tests is not "done" for a change to a
user interface. jsdom cannot tell you that a control is a dead end, that a tab
is clipped, or that a panel is blank. Before a UI change is called complete,
every line below holds for the flow it touched.

The checklist was not written from first principles. It was written after an
audit of a working editor (blobert's asset editor, fixed in laazer/blobert#26)
found six places where a user could get stuck, and each rule below that came
out of that audit says which finding it answers. A rule with its reason
attached survives the first time it is inconvenient; a bare rule does not.

## The checklist

**Every reachable state is designed.** Empty, loading, error and "nothing
selected" each say *why* and *what to do next*. Never a blank panel, a
placeholder with no way forward, or a control that is silently inert.

> The audit's first two findings were one bug: the code that loaded a preview
> only ran while a particular tab was open, so on the default tab the viewport
> read "No model loaded — run a generation command" for something that was
> simply still loading. Loading and "nothing" looked the same, and the message
> told the user to do something they did not need to do. Elsewhere three
> panels described one empty library three different ways, none of them
> pointing at the action that would fill it: one condition gets one message.

**No dead controls.** A control that cannot act is hidden, or disabled with
the reason visible on the page. If it is enabled, using it does something.

> The audit found a playback bar whose buttons were live with nothing loaded,
> and a Duplicate action that was permanently disabled with its reason only in
> a tooltip, for an API that did not exist yet. The fix disabled the first with
> a caption and removed the second until it could work.

**Every action gives feedback**, from pending to success or failure.
Destructive actions confirm.

**Nothing is partially cut off.** Buttons, tabs, toolbars and inputs either fit
their container or the container scrolls properly. A tab strip that clips its
last tab, or a row of buttons squeezed past legibility, is a bug. Check at the
widths the app's own chrome defines for its panels, and at a narrow window.

> A tab that existed only to show a developer note was clipping the tab next
> to it. Removing the tab fixed both.

**Long lists of selectable things are compressed.** A list of parts,
materials, versions or anything else a user picks from uses a control that
keeps it short: filter or search, collapsible groups, a select or combobox, or
a scrolling region with a fixed height. Not an unbounded stack of rows that
pushes the rest of the panel off screen.

**The flow reads on its own.** A first-time user can follow the panel top to
bottom without tooltips, "(i)" popovers or help text explaining what a control
is for. A control that needs an info button to make sense is renamed,
relabelled, reordered or removed.

**What you edit is what you get.** The preview reflects the current state of
what is being edited. A stale or fallback preview is labelled as such.

> Switching the selection kept the previous item's model in the viewport, so
> the user was looking at one thing while editing another.

**Reuse the app's own chrome.** New panels take their colours, spacing and
type from the app's tokens and compose its existing controls. No bespoke
colours, fonts or one-off button styles.

**Deep links and refresh restore the same view.** A URL that names a
selection lands every panel on that selection, and a link naming something
that does not exist says so plainly, rather than surfacing a raw status code
while the panels disagree about what is selected.

> A deep link to a missing item produced a bare 403 in one panel while two
> others showed a different selection. The fix made the link set every panel's
> selection, and made the failure name the path and the reason.

## Walk it in a browser

This is not optional. Start the app, open it in a browser, and walk the
changed flow end to end: the empty and error paths above, and the narrow
width. Then say so in the pull request or commit body with a line of the form
`Verified in the browser: …`, naming what was exercised. A UI change without
that line is not done, because nothing else in the pipeline looked at it.

## What the gate checks, and what it cannot

The UX-states gate (`gates/lore_eden_gates/ts_ux_states_check.cjs`) is the
mechanical half of this checklist. On the TypeScript it can parse, it refuses
four shapes, all diff-scoped:

| Shape | The line above it enforces |
|---|---|
| A button or link whose only children are icons, with no text and no accessible name (`aria-label`, `title`, or a child's `alt`) | The flow reads on its own |
| An `onClick` on a non-interactive element with no role, `tabIndex` and key handler | No dead controls — for a keyboard user |
| A click-to-close backdrop in a file that never handles Escape | No dead controls — for a keyboard user |
| A `.map()` over fetched rows in a file with no empty-case branch | Every reachable state is designed |

Its waiver is a `ux-ok:` comment on or above the line, with a reason of
substance; the bare marker does not waive anything.

Everything else here is a reading. The gate cannot tell whether a message
says what to do next, whether a tab is clipped at a given width, whether a
list is too long, whether a preview is stale, or whether a deep link lands
every panel in the same place. Passing the gate means the four shapes are
absent; it does not mean the change is done.
