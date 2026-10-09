# lore-eden gates

Repo-agnostic organization, safety and diff-scoping gates, runnable from lefthook
pre-commit hooks and from orchestration stage transitions.

Extracted from loregarden, where they already ran against several repositories —
but by absolute filesystem path, with no versioning, while two other projects ran
stale vendored forks of the same files. This package is where they live now.

## Install into a repository

The repo needs a `lefthook.yml` with a `pre-commit:` → `commands:` map. Then:

```bash
gates/scripts/install-workspace-hooks.sh /path/to/repo
```

That writes a marker-delimited managed block into the target's `lefthook.yml`,
pointing at this checkout's scripts rather than copying them in — a copy in each
repo is a copy that drifts. Re-run to refresh; `--check` reports drift without
writing. A block written by loregarden's predecessor of this installer is
replaced, not stacked beside.

For the TypeScript gate, install this package's own dependencies once:

```bash
cd gates && npm install
```

The gate resolves its parser from here first, then from the graded repo, and
fails loudly if neither has one. It never skips: a gate that cannot parse cannot
report a file clean.

## The gates

| Script | Enforces |
|---|---|
| `py_organization_check.py` | File/class size caps (growth-only), `__init__` minimalism, duplicate function bodies, dynamic `getattr`/`setattr`, `isinstance`, string vocabularies that should be enums |
| `py_silent_except_check.py` | Broad `except` with an inert body — reports a success the code never had |
| `py_git_subprocess_check.py` | `git`/`gh` subprocess calls routed through an env-scrubbing wrapper (opt-in, see below) |
| `py_defensive_normalization_check.py` | `str(x).strip().lower()` in a comparison — re-normalizing a value that should be constrained at its source |
| `ts_organization_check.cjs` | File size caps, no `fetch`/`axios` in `.tsx`, duplicate bodies, cross-codebase DRY, barrel size, inline `instanceof Error` ternaries |
| `ts_no_silent_failures_check.cjs` | A `catch` that neither rethrows, records nor surfaces; a console-only `catch`; `.catch(() => {})` / `.catch(console.error)`; `Promise.all`/`allSettled` over raw `fetch` with no `.ok` check in the file |
| `ts_ux_states_check.cjs` | An icon-only `<button>`/`<a>` with no accessible name; `onClick` a keyboard cannot reach; a `role="presentation"` backdrop in a file that never handles Escape; a fetched list `.map()`ped with no empty case. The mechanical half of `policy/ux-definition-of-done.md` — the rest of that checklist is a reading |

| `ruff_complexity_diff_filter.py` | C901 complexity, but only where a touched function's complexity **grew** |
| `pylint_diff_filter.py` | `too-many-statements`, same don't-make-it-worse policy |

The last two need `ruff` and `pylint` on the machine. Absent, they **refuse and
exit non-zero** — they used to print "no growth on touched lines" and exit 0,
which meant a machine missing the tool reported a pass on every commit
indefinitely.

Supporting, not installed as hooks:

| Script | Purpose |
|---|---|
| `precommit_git_diff.py` | The shared diff/scope harness every Python gate imports. Scrubs `GIT_DIR`/`GIT_WORK_TREE`, decodes `core.quotePath` escapes, resolves scopes, and refuses to call an unresolved scope a pass |
| `ts_gate_harness.cjs` | What every TypeScript gate shares: the parser resolution chain, scope (asked of `precommit_git_diff.py`, never re-derived), the guarded file read, argv, and the one exit path for "could not examine" |
| `ts_waivers.cjs` | The marker-with-a-reason waiver contract the TypeScript gates share |
| `select_pytest_targets.py` | Import-graph test selection for pre-push, biased hard toward over-running |

## Profiling this suite

This suite can be profiled like the python one. It reaches
`lore_eden.testing.pytest_profile` by path rather than by install, so it stays
dependency-free:

```
cd gates && LORE_EDEN_PROFILE=../.profile/gates.json python -m pytest -q
```

A requested profile that cannot be produced fails the run rather than passing
quietly. Unset, the hook does nothing and imports nothing.

## Scopes

Every gate takes `--repo PATH` and `--scope staged|worktree|branch`.

`worktree` includes untracked files. That is deliberate: at a stage transition an
agent's edits are uncommitted, and a module it just wrote is the least-reviewed
code in the run — a scope that skipped it would grade everything except the thing
most worth grading.

An unrecognized scope is an error, never coerced to a default. A run that could
not work out what to examine has not examined anything, and must not leave by the
success exit.

## Configuration

Two rules name a helper the repo is expected to have, and neither can guess it.
Both stay **off until configured**, in `.lore-eden-gates.json` at the repo root:

```json
{
  "mid_dot_helper": "myapp.dot_line.Dot / mid_dot",
  "git_subprocess_helper": "myapp.services.git_subprocess.run_git",
  "git_subprocess_helper_path": "myapp/services/git_subprocess.py"
}
```

- **`mid_dot_helper`** — enables the rule against hand-rolling several `" · "`
  labels in one function. The Python package ships a helper that satisfies it,
  so a repo already depending on `lore_eden` can name
  `"lore_eden.dot_line.Dot / mid_dot"` rather than writing its own; see
  *Log lines a person can read* in `python/README.md`.
- **`git_subprocess_helper`** / **`_path`** — enables the git-routing rule.
  `GIT_DIR` overrides `cwd`, so a `subprocess.run(["git", ...], cwd=repo)` that
  passes the ambient environment through operates on whatever repository the
  parent was bound to. The `_path` exempts the wrapper itself, which is the one
  file allowed to build a raw git argv. `lore_eden.git.run_git` is a ready
  implementation, which is what this repo points itself at.

These two rules and the helpers that satisfy them ship in the same repository
but install by different routes — the gates by filesystem path, the package by
pip — so neither imports the other, and a repo may adopt the gates without the
package. Naming a helper you do not have is the one configuration that makes a
rule worse than leaving it off: every finding would name a module the reader
cannot open.

An absent file means no house rules. A file that exists but is malformed, or
carries an unknown key, **fails the gate** rather than falling back to defaults —
a typo that silently disables a check is the exact failure this library exists to
remove.

JSON rather than TOML because the gates are invoked as plain `python3` against
arbitrary repos and `tomllib` only exists from 3.11.

## Waivers

Per-line, on the offending line, and each names the rule it waives:

- `# py-org: allow-string`, `# py-org: allow-isinstance`, `# py-org: allow-dynamic`
- `# py-silent: allow`
- `# py-defensive: allow`
- `// ts-org: allow-instanceof`
- `// silent-ok: <reason>` — in a comment, with a reason of at least 12
  characters. The marker alone, or a shrug of a reason, is itself a finding.
- `{/* ux-ok: <reason> */}` — the same contract, from the same module.

The TypeScript waivers are not strictly per-line: a `silent-ok:`/`ux-ok:`
marker waives **one construct** the gate grades (a `catch`, a `.catch(...)`
call, a `<button>`, …) — the innermost of those that contains the comment
(a catch body, a `.catch` handler, a JSX child), starts on the comment's line,
or starts on the line directly below the comment block the marker is in. So a
reason written for an inner, retried probe does not also excuse the `catch`
around it, and a reason inside a catch body or on the lines above it still
reaches the catch.

Two rule changes were made during extraction, both because the gates failed on
their own source. Worth being precise about why that had not happened before:
the rules are diff-scoped, so in the repo they were written in these lines were
old and never fired. Extraction made every line new, which is the first time
either rule was pointed at an AST walker.

- **`isinstance(node, ast.Something)` is exempt without a waiver.** Both of that
  rule's remediations are unactionable against the stdlib AST — you cannot model
  an `ast.Call` with Pydantic, and you cannot add a method to it to dispatch on.
  Type-testing nodes *is* the visitor idiom Python offers. Checks against
  builtin payload shapes (`dict`, `str`, …) are still flagged.
- **`# py-org: allow-dynamic` is new.** The `getattr`/`setattr` rule shipped with
  no escape hatch, which left reaching for an optional attribute on a foreign
  object — an AST node that may not carry `end_lineno` — with no answer short of
  disabling the gate.

## Tests

```bash
cd gates && python3 -m pytest
```

The suite builds real disposable git repositories rather than mocking one, in
three different layouts — Python under `server/`, under `asset_generation/`, and
at the repo root — because "it detects layout" is the load-bearing claim here and
a mocked diff would test the half that was never in question.

## Two gates are off until the repo names its helper

`py_git_subprocess_check` and `py_defensive_normalization_check` need to know
what *this* repo's designated wrapper is, and no library can guess that. Give
them a `.lore-eden-gates.json` at the repo root:

```json
{
  "git_subprocess_helper": "myapp.services.git_subprocess.run_git",
  "git_subprocess_helper_path": "myapp/services/git_subprocess.py"
}
```

Without it the git-subprocess gate still **runs**; what it loses is the ability
to enforce. It reports the unscrubbed calls it finds and exits 0, because
demanding they route through a wrapper the repo has not got would fail every
commit from the moment the gate is installed — an outage, not a stricter gate.
Naming the helper is what turns the report into a blocking rule.

It used to return before reading a file, and print `skipped`. That collapsed two
opposite facts into one line: a repo that never shells out to git, and a repo
that does it in twelve places with no chokepoint. The first is the rule holding,
verifiably, and a gate that can say so should.

The notice reaches both invocation forms. It once appeared only in the
`--repo/--scope` form, which meant a repo that installed five gates silently ran
four, and the one it lost was the one it had most deliberately asked for.

An unknown key or a malformed file raises rather than defaulting. A repo that
meant to configure a gate and typed the key wrongly should hear about it, not
get the unconfigured behaviour it was trying to leave.

## Declining a gate the repo already has

A repo that already runs its own equivalent must be able to say no, or
installing this block puts two rule sets over the same staged files — the
double-gating this library exists to prevent.

```json
{
  "excluded_gates": {
    "lore-eden-py-organization": "we run our own via task server:organize:changed"
  }
}
```

The exclusion lives in the target repo's config rather than in an installer
flag, because **the managed block is regenerated on every install**: a
hand-edit, or a one-off `--exclude`, would be silently undone by the next
refresh. The reason is written into the block itself, so a gate missing from it
reads as a decision rather than as an install that went wrong. An unknown gate
name is refused and nothing is written — a typo that silently installed the gate
the repo meant to decline is the failure this prevents.

Found cutting loremaker over: its `lefthook.yml` names no gate commands, so the
ticket recorded it collision-free, but `server-pre-commit.sh` reaches
`task server:organize:changed` → `server-organize-changed.sh` → its own
313-line `py_organization_check.py`.

## Checking a repo without changing it

```bash
gates/scripts/install-workspace-hooks.sh --check /path/to/repo
```

Reports `missing`, `outdated` or `ok` and writes nothing — so you can see what an
install would do before doing it. Re-running the installer on a repo that already
has the block refreshes it in place; a block written by loregarden's predecessor
of this script is **replaced, not stacked beside**.

## The fork with loregarden's copies, reconciled

loregarden does not consume this library. It carries its own copies of these
scripts under `.lefthook/scripts/`, and both sides were improved without the
other knowing — which is the failure mode the library exists to end. Before any
repo can be switched over, neither side may be ahead, or installing the managed
block would *regress* the repo it was installed into.

Ported here from loregarden's copies:

| what | where |
|---|---|
| an unborn HEAD is gradable, not unexaminable | `unborn_worktree`, and the four ref-based queries that now use it |
| a diff that emitted a header but described nothing | `suppressed_diff_paths`, now parsed-count vs numstat-count |
| the scope resolved once, for a caller in another language | `--emit-scope-json`, and the 483 lines it deleted from the `.cjs` |

Two claims that prompted this turned out to be stale and were **not** ported,
because the fix was already here: the symlink-cycle `RuntimeError` (ELOOP) catch
and the resolved-prefix repository root, both in `read_source_text`. They were
compared line by line rather than taken on trust.

The trees still differ, and these are the reasons:

* **Typing spellings.** This tree is PEP 604/585 throughout (`X | None`,
  `list[str]`); loregarden's copies are `Optional[X]`, `List[str]`. Same
  behaviour. This side is the one under `policy/ruff-base.toml`.
* **`require_tool_ran`, and the `_cli` wrappers on the two diff filters.** Only
  here. A `python -m ruff` with ruff uninstalled exits non-zero with nothing on
  stdout, and `json.loads(stdout or "[]")` turns that into "clean" — a machine
  missing the tool reported a pass on every commit.
* **The interpreter guard.** `interpreter.require_python()` here;
  `gate_python_guard.require_supported_python()`, requiring 3.11 and exiting 69,
  there. Two implementations of one idea; theirs is tied to their runner.
* **Generalised gates.** `py_git_subprocess_check`, `pylint_diff_filter` and
  `select_pytest_targets` take their repo's helper, prefix and package as
  configuration here and hardcode loregarden's there. `py_organization_check`
  carries loregarden's `Dot`/`mid_dot` rules as house rules rather than as
  built-ins.
* **Parser resolution in the `.cjs`.** Here it tries this package's own
  `node_modules`, then the graded repo's, then throws with what it tried;
  loregarden's resolves `../../client` by relative path, which is what made the
  gate un-extractable in the first place.
* **How the `.cjs` invokes the resolver.** A bare `python3` here — the resolver
  needs no tool, so it skips the `gate-python.sh` the installed block runs the
  Python gates through; `bash server_python.sh` there.
* **`relOf` in the `.cjs`.** Here it mirrors `located_path` — both sides
  real-pathed, the file's parent only. loregarden's compares against the
  unresolved root, so a checkout behind a symlinked prefix (macOS `/tmp` ->
  `/private/tmp`, an agent worktree under a linked home) matches no key and the
  gate prints a credible file count with a pass. **That one is a live bug on
  their side**, introduced with their rewrite and fixed here.
* **Dead constants in loregarden's `.cjs`.** `C_ESCAPES`,
  `GIT_LOCATION_ENV_VARS`, `GIT_CONFIG_ENV_PREFIXES`, `TRUNK_REF_CANDIDATES`,
  `tsFilesInScope` and an orphaned doc block survived their rewrite unreferenced.
## TypeScript failures that nobody sees

`ts_no_silent_failures_check` is `py_silent_except_check` for TypeScript. Before
it, a `catch {}` in a `.tsx` file passed every gate here, including in this
repo's own `ts/` kit. Four shapes, diff-scoped:

| shape | why it is a finding |
|---|---|
| a `catch` that neither rethrows, records state, nor surfaces | the user reads "no data" where the truth was "it failed" |
| a `catch` whose only effect is `console.*` | a line in devtools is not something a user sees |
| `.catch(() => {})`, `.catch(console.error)` | the rejection is discarded |
| `Promise.all`/`allSettled` over raw `fetch`, no `.ok` read in the file | `fetch` resolves on a 500, so "0 failed" is reported when all of them did |

"Records" means assigning outward, a `setX(...)` setter, returning a value, or
collecting the caught error into something read later (`attempts.push(err)`,
`failures.set(id, String(err))` — the error, or something built from it, must
be an argument; `seen.add("x")` records nothing about the failure). "Surfaces" is a
call to one of loregarden's names for it — `pushToast`, `captureException` and
kin — because that is where the gate was written; a repo with other names
passes by recording, rethrowing or waiving.

`.catch((e) => void report(e))` is a report, not a discard: `void` of an
expression counts as discarding only when it neither calls anything nor reads
the rejection (`void 0`, `void null`).

A waiver is `silent-ok:` in a **comment**, belonging to the construct it
annotates (see [Waivers](#waivers)), with a reason of at least 12 characters. A
marker inside a string is not a waiver.

Extracted from loregarden, it and the organization gate now share
`ts_gate_harness.cjs` rather than each carrying a parser loader and a diff
reader. The source's `ts_git_diff.cjs` was not ported: its scope logic was a
third copy of `precommit_git_diff.py`'s, and the harness asks that module
instead. Defects fixed on the way, each with a test that fails against the
source's behaviour:

* **Every file parsed as JSX.** A generic arrow in a `.ts` file did not parse,
  and the source skipped unparseable files — so the file, and the empty
  `catch` in it, passed. Now JSX is by extension, and a file that does not
  parse is refused.
* **An unknown scope became `staged`.** `--scope wrokTree` graded an empty index
  and exited 0. It is refused now.
* **`console.error(describeError(err))` counted as surfacing**, because the walk
  descended into the console call's arguments.
* **A local declared and dropped counted as recording.**
  `catch (err) { const message = String(err); }` passed.
* **`.ok` was searched only inside the `Promise.all` call**, though the source's
  own header said "in the file". The usual correct shape reads `.ok` after the
  await, and was reported.
* **A multi-line block-comment waiver above a `.catch` was ignored**: comment
  lines were found by prefix, and a block's continuation line starts with
  prose. loregarden's UX gate had fixed this; its silent-failure gate had not.
* **Collecting a failure to report later did not count.** The harness's own
  parser resolution chain — `catch (err) { attempts.push(...) }`, all thrown
  together at the end — failed the gate the first time it graded this package.

Fixed in review, each with a test:

* **Any `.push`/`.add`/`.set` counted as recording the failure**, so
  `catch { seen.add("x"); }` and `url.searchParams.set(...)` passed. The call
  must now carry the caught error.
* **Every `void <expr>` handler was a discard**, so
  `.catch((e) => void report(e))` failed.
* **A waiver anywhere in a span waived it**, so a marker on a nested catch hid
  the catch around it. A waiver now belongs to one construct (above).

`--all` (grade every file regardless of the diff) was not carried over; no
other gate here has it, and `--scope worktree` on an untracked tree does the
same job.

## The four UX states a review cannot see

`ts_ux_states_check` is the mechanical half of
`policy/ux-definition-of-done.md`; the rest of that checklist is a reading, and
the gate says so under every failure. Four shapes, diff-scoped, on `.ts`/`.tsx`:

| shape | why a review misses it |
|---|---|
| `<button>`/`<a>` whose only children are icons, with no text, `aria-label` or `title` | the reviewer already knows what the icon means; a screen reader says "button" |
| `onClick` on a non-interactive element without `role`, `tabIndex` and a key handler | a mouse reaches it, a keyboard does not |
| a `role="presentation"` backdrop with `onClick`, in a file that never handles Escape | the mouse can dismiss it and a keyboard inside the focus trap cannot |
| a `.map()` over fetched rows in a file with no empty-case branch | zero rows and a failed load are the same blank pane |

Most of the value is in what it does *not* report, and that reasoning came
across unchanged: `label` and `option` are clicked through the control they
belong to; a `role="presentation"` backdrop owes Escape rather than `tabIndex`;
a `role="dialog"` panel carrying `stopPropagation` is structure, not a
control; a component may render a button inside, and the gate cannot see in;
a `{expr}` child may be a label, and a gate that cannot tell does not accuse —
nor does a component child (`<FormattedMessage id="save" />`) unless it is
recognisably an icon: named `…Icon`, `Icon…` or `Icons.…`, or imported from an
icon package (`lucide-react`, `react-icons/*`, `@heroicons/*`, …);
options in a `<select>` are a disabled picker, not a blank pane.

It shares `ts_gate_harness.cjs` and `ts_waivers.cjs` with the silent-failure
gate. Defects fixed in extraction, each with a test that fails against the
source:

* **`<button><img alt="Delete row" /></button>` was reported as unnamed.**
  Accessible-name computation takes a child's `alt`, `aria-label` or `title` as
  the text it contributes. `alt=""` is the explicit "decorative" and still
  fails.
* **A short waiver was reported once per touched line beneath it** — three
  identical findings for one `// ux-ok: meh` over a three-line span.
* **JSX for every file**, and a skipped unparseable file, as in the
  silent-failure gate above.

Fixed in review, each with a test: a childless component child was read as
"no text", so `<button><FormattedMessage id="save" /></button>` failed; and a
`ux-ok:` on a nested element waived the control around it.

## CSS

`css_organization_check` grades `.css`, which every other gate here ignored. The
globs named `.py`, `.ts` and `.tsx`, so a commit touching only stylesheets ran
the whole pre-commit stage in under a tenth of a second and printed "no files
for inspection" for each gate. That is a clean run over nothing at all.

Five rules, diff-scoped like the rest:

| rule | what it catches |
|---|---|
| `undefined-token` | `var(--fsBody)` where the token is `--fs-body`. CSS has no error for this: the declaration is invalid at computed-value time and is dropped silently. Needs `css_token_source`. |
| `token-duplicate-colour` | a colour literal equal to a token's value, in hex or `rgb()`. The same colour in two notations moves in one place and not the other. |
| `file-length` | over 600 lines, and only on net growth. |
| `important` | `!important` without a waiver naming a third-party package — and the gate checks the package is a real dependency and not one of ours. |
| `orphan` | a stylesheet nothing imports. |

Waivers go on the offending line or the one above it:

```css
color: #6fae8f;          /* css-org: allow-colour (matching a screenshot) */
z-index: 9 !important;   /* css-org: allow-important (react-datepicker) */
```

`css_motion_check` grades the same files for motion. It is ported from
loregarden's `ts_motion_check.cjs`, so a repo running these gates holds the line
loregarden does:

| rule | what it catches |
|---|---|
| layout property | a transition or keyframe on `width`, `top`, `margin`, …, or `transition: all`. Each frame relays out the page; animate `transform` and `opacity`. |
| hardcoded duration | a UI duration of 400ms or less where the repo defines `--t-fast` (in `css_token_source` or the root `index.css`). Use the token. Longer ambient loops are exempt. |
| reduced motion | motion in a file when neither the root stylesheet (a `prefers-reduced-motion` rule over `*`) nor the file itself has a reduced-motion rule. |

Waive with `motion-ok:` and a reason, on the line or in the comment above it.
`!important` inside a `prefers-reduced-motion` block needs no waiver from
`css_organization_check`, because a reader's setting has to beat inline styles.

Point the token rules at whatever file defines the repo's custom properties — a
`.css` with `--name: value` declarations, or a TS/JS module carrying
`css`/`value` pairs:

```json
{ "css_token_source": "ts/src/tokens/specs.ts" }
```

Without it those two rules are off, and the gate says so on every run.

## Shell

`sh_shellcheck_check` drives shellcheck over `.sh`, diff-scoped like the rest.
It closed a gap the coverage check below had recorded as an exemption: nine
tracked scripts, two of them the ones git executes on every commit and push,
and nothing that had ever read one.

shellcheck arrives as a pip wheel (`shellcheck-py`), so this needs no system
package — the same shape as the two diff filters, which drive `ruff` and
`pylint`. Absent, it refuses rather than reporting no findings: "the linter is
not installed" and "the scripts are clean" produce identical output.

Two flags are load-bearing, and neither is optional. Without `-x` shellcheck
will not open a `source`d file and reports SC1091 on every script that has one.
Without `--source-path=SCRIPTDIR` it resolves the `# shellcheck source=`
directive against the current directory rather than the script's own, and still
cannot find it. The pair is the difference between three notes nobody can act on
and a clean run.

Findings at `error`, `warning` and `info` fail; `style` does not. `info` is in
deliberately: SC2086 — an unquoted variable that word-splits, the most common
shell defect there is — is `info`, and shellcheck's gcc format collapses `info`
and `style` into one label. A first draft read gcc output, excluded notes, and
passed a planted `rm $UNQUOTED`. Reading JSON is what makes the two separable.

## Configuration and lockfiles

`data_format_check` parses `.json`, `.yml`/`.yaml` and `.toml`. They were exempt
with the reason "malformed JSON fails the tool that reads it" — true of a file
something reads every run, false of a workflow not read until a push or a gate
config not read until a gate runs in another repo. "Some other tool would notice
eventually" is not a check.

Whole-file rather than diff-scoped, and it is the one gate here that should be:
a file either parses or it does not, and a malformed line nobody touched still
breaks every reader.

JSON is checked always — `json` is standard library on every interpreter these
gates support. YAML and TOML are not: `tomllib` arrives in 3.11 and these gates
floor at 3.10, and PyYAML is third-party. When a parser is missing the run says
so by name and names the fix, rather than skipping in silence. Refusing outright
would make the gate unusable on the bare `python3` this library exists to run on.

## Is anything looking at this file at all?

`gate_coverage_check` asks the question one level up from the others. It reads
the index, maps each tracked path to the gate that grades its suffix, and fails
on any path nothing grades and nothing has excused.

An uncovered path is not automatically a defect — plenty of file types need no
gate. What they need is for somebody to have decided, which is what the
configuration records:

```json
{
  "ungated_globs": {
    "*.md": "prose; no markdown linter this repo has agreed to",
    "LICENSE": "verbatim licence text"
  }
}
```

A reason, not a bare list: an exemption nobody had to justify is how a file type
goes ungraded for a year without anyone choosing it. An exemption that matches
nothing fails too, so the list describes the repo rather than its history.

It is not diff-scoped — coverage is a whole-repository property — and it reads
the index rather than the files, so it costs milliseconds.
