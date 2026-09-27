# Shared policy

Lint and duplication configuration that was already being copied between repos,
in one place — with the drift it accumulated resolved rather than averaged. The
standards that are prose rather than configuration live here too, beside the
gate or config that enforces their mechanical half.

| File | What it settles |
|---|---|
| `pylint.toml` | One enabled check, and why only one |
| `ruff-base.toml` | The rules every project starts from |
| `ruff-web-framework.toml` | Opt-in accommodations for FastAPI/SQLModel idioms |
| `oxlintrc.json` | React/TypeScript baseline |
| `jscpd.json` + `jscpd.md` | What counts as a duplicate — see the decision record |
| `ux-definition-of-done.md` | When a UI change is done; the prose half of the UX-states gate |

## Using them

These are configuration, not code, so there is no install. Copy the block into
your `pyproject.toml`, or point the tool at the file:

```bash
pylint --rcfile path/to/lore-eden/policy/pylint.toml src/
ruff check --config path/to/lore-eden/policy/ruff-base.toml .
jscpd --config path/to/lore-eden/policy/jscpd.json
```

A project using FastAPI or SQLModel merges `ruff-web-framework.toml`'s `ignore`
list into the base's. It is separate rather than folded in because those are
genuine findings that two specific frameworks make idiomatic — a project not
using them should keep the checks, and quietly weakening every project's lint to
accommodate a dependency most do not have is how a shared baseline stops meaning
anything.

`ux-definition-of-done.md` is read, not run. Point your contributor guide or
agent instructions at it rather than copying it, so there is one version of it;
the gate that checks the parts a parser can see is
`gates/lore_eden_gates/ts_ux_states_check.cjs`.

## Adopting on a repo that already has findings

Do not turn the rules down to meet the code. Two mechanisms exist for exactly
this, and both are honest where a loosened rule is not:

- **The organization gates are diff-scoped.** A file already over a limit does
  not block an unrelated edit; only growth fails.
- **jscpd's `threshold` takes today's number.** Set it to the current
  percentage, commit that, and ratchet toward zero.

A rule turned down to pass is a rule that has stopped measuring anything, and
nothing records that it used to.

## What is deliberately not here

`mypy` settings. The source's carried a 21-entry `disable_error_code` list
described in its own comment as a gradual-typing debt snapshot to tighten as
modules get annotated. That is one codebase's position in a migration, not a
policy, and shipping it as shared would hand every adopter someone else's debt
as a starting point.
