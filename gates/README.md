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
* **How the `.cjs` invokes the resolver.** A bare `python3` here, matching the
  installed lefthook block and every Python gate; `bash server_python.sh` there.
* **`relOf` in the `.cjs`.** Here it mirrors `located_path` — both sides
  real-pathed, the file's parent only. loregarden's compares against the
  unresolved root, so a checkout behind a symlinked prefix (macOS `/tmp` ->
  `/private/tmp`, an agent worktree under a linked home) matches no key and the
  gate prints a credible file count with a pass. **That one is a live bug on
  their side**, introduced with their rewrite and fixed here.
* **Dead constants in loregarden's `.cjs`.** `C_ESCAPES`,
  `GIT_LOCATION_ENV_VARS`, `GIT_CONFIG_ENV_PREFIXES`, `TRUNK_REF_CANDIDATES`,
  `tsFilesInScope` and an orphaned doc block survived their rewrite unreferenced.
