# Contributing

## Extraction rules

Every module here arrives by extraction from a working project. These rules are
load-bearing rather than aspirational — they are how the source projects keep
running while this is built:

1. **Copy, don't move.** An extraction copies code here. It does not delete or
   rewrite the source file. The source projects keep running untouched.
2. **Cut-over is separate, one component at a time.** Its bar is "the consuming
   project's existing test suite is still green after switching to lore-eden" —
   not "lore-eden exists." Rollback is reverting one commit; the original code is
   still there until a later cleanup.
3. **Consumed by path link while the API moves.** No publishing until the
   interfaces have stabilized against a second consumer.
4. **Every cut-over runs the consuming project's own gates**, not just these.
5. **This repo grades itself with its own gates.** If they cannot be lived with
   here, they should not be installed anywhere else.

## Fix what the extraction exposes

Reading two implementations of the same thing side by side finds defects that
neither project had a reason to notice. Fix them here rather than copying them
forward, and give each one a test that fails against the original behaviour — a
comment saying the source was wrong is not evidence, and a future reader has no
other way to tell a deliberate difference from a transcription error.

Say so in the pull request too, with the concrete symptom. "The border property
names were assembled by string concatenation, so a corner override produced
`borderTopleftRadius`, which React drops silently" is reviewable. "Cleaned up
border handling" is not.

Where the source depended on a library, **the library is the specification**.
Reproducing its arithmetic by reading it is not enough: hand-written colour
conversions here matched the source's algorithm as documented and still differed
by one on real input, because `Math.round(-25.5)` is `-25`.

## Preserve behaviour that is a choice

Not every surprise is a bug. The responsive resolver searches outward in both
directions rather than cascading down, so a config naming only `xl` still answers
at `xs`. That is unusual and it is deliberate; changing it would silently drop
values at sizes their authors never named. Distinguish a decision you disagree
with from a defect, and carry the first across with the reasoning attached.

## Checks

Each package is checked independently, and CI runs all three:

```bash
cd python && pytest
cd ts && npx tsc --noEmit && npx vitest run
cd gates && python3 -m pytest
```

The gates also grade this repository. Run one directly rather than spending a
commit to find out what it will say:

```bash
python3 gates/lore_eden_gates/py_organization_check.py --repo . --scope worktree
node gates/lore_eden_gates/ts_organization_check.cjs --repo . --scope worktree
```

A gate your change trips is part of your change, including when the file was
already over a limit before you touched it — the caps read the whole file, which
is how one that has been growing unchecked finally gets split.
