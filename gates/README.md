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
| `select_pytest_targets.py` | Import-graph test selection for pre-push, biased hard toward over-running |

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

**Without it the git-subprocess gate does nothing**, and says so on every run —
in both the pre-commit form and the `--repo/--scope` form. It once announced the
skip only in the second, which meant a repo that installed five gates silently
ran four, and the one it lost was the one it had most deliberately asked for.

An unknown key or a malformed file raises rather than defaulting. A repo that
meant to configure a gate and typed the key wrongly should hear about it, not
get the unconfigured behaviour it was trying to leave.

## Checking a repo without changing it

```bash
gates/scripts/install-workspace-hooks.sh --check /path/to/repo
```

Reports `missing`, `outdated` or `ok` and writes nothing — so you can see what an
install would do before doing it. Re-running the installer on a repo that already
has the block refreshes it in place; a block written by loregarden's predecessor
of this script is **replaced, not stacked beside**.

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

Point the token rules at whatever file defines the repo's custom properties — a
`.css` with `--name: value` declarations, or a TS/JS module carrying
`css`/`value` pairs:

```json
{ "css_token_source": "ts/src/tokens/specs.ts" }
```

Without it those two rules are off, and the gate says so on every run.

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
