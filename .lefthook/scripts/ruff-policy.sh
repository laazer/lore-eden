#!/usr/bin/env bash
# The repository, under the ruff policy it publishes to everyone else.
#
# Whole-tree rather than diff-scoped, which is the opposite of what the six gate
# commands do — and deliberately so. They are diff-scoped because a whole-file
# check on a codebase with pre-existing hits fails every unrelated edit, which is
# how a gate teaches people to bypass it. That objection does not apply here:
# lor-extract-lore-45 and -47 cleaned the tree, so a whole-tree run passes today
# and can only fail on something newly introduced.
#
# It costs about 0.2s. Diff-scoping it would add a moving part to save nothing,
# and would be strictly weaker than the CI step that runs the same command.
set -euo pipefail

# shellcheck source=hook-noninteractive.sh
source "$(cd "$(dirname "$0")" && pwd)/hook-noninteractive.sh"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

if [ -x "$ROOT/python/.venv/bin/ruff" ]; then
  RUFF=("$ROOT/python/.venv/bin/ruff")
elif command -v ruff >/dev/null 2>&1; then
  RUFF=(ruff)
else
  # Refuse rather than skip. A missing linter that reports nothing is
  # indistinguishable from a clean tree — the same failure the diff filters
  # already refuse to make.
  echo "pre-commit: ruff is not installed, so the policy check cannot run." >&2
  echo "Run: cd python && .venv/bin/pip install ruff" >&2
  exit 1
fi

cd "$ROOT"
exec "${RUFF[@]}" check --config policy/ruff-base.toml gates python
