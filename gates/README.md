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

Supporting, not installed as hooks:

| Script | Purpose |
|---|---|
| `precommit_git_diff.py` | The shared diff/scope harness every Python gate imports. Scrubs `GIT_DIR`/`GIT_WORK_TREE`, decodes `core.quotePath` escapes, resolves scopes, and refuses to call an unresolved scope a pass |
| `select_pytest_targets.py` | Import-graph test selection for pre-push, biased hard toward over-running |
| `ruff_complexity_diff_filter.py`, `pylint_diff_filter.py` | Diff-scope C901 and `too-many-statements` to functions this change actually grew |

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
  labels in one function.
- **`git_subprocess_helper`** / **`_path`** — enables the git-routing rule.
  `GIT_DIR` overrides `cwd`, so a `subprocess.run(["git", ...], cwd=repo)` that
  passes the ambient environment through operates on whatever repository the
  parent was bound to. The `_path` exempts the wrapper itself, which is the one
  file allowed to build a raw git argv.

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
