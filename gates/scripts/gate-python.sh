#!/usr/bin/env bash
# Run a Python gate under the interpreter that has its tools.
#
# The pylint and ruff filters run their tool as `sys.executable -m …`, and the
# shell gate finds `shellcheck` on PATH. `scripts/bootstrap-worktree.sh`
# installs all three into python/.venv — so under a bare `python3`, whichever
# is first on PATH, both the pylint and shellcheck gates refused every commit
# on a fully bootstrapped checkout.
#
# The venv is the one beside these gates, not the target repo's: a workspace
# installing lore-eden's gates gets lore-eden's toolchain with them.
#
# Usage: gate-python.sh <gate-script> [args...]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV="$ROOT/python/.venv"

# A git worktree is created without a venv, and git hooks are shared across
# worktrees — so fall back to the primary checkout's, whose tools are the same.
# GIT_DIR is unset because a hook exports it, and it beats -C.
if [ ! -x "$VENV/bin/python" ]; then
  common="$(env -u GIT_DIR -u GIT_WORK_TREE git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
  if [ -n "$common" ] && [ -x "$(dirname "$common")/python/.venv/bin/python" ]; then
    VENV="$(dirname "$common")/python/.venv"
  fi
fi

if [ -x "$VENV/bin/python" ]; then
  PATH="$VENV/bin:$PATH"
  export PATH
  exec "$VENV/bin/python" "$@"
fi

# Not a refusal: gates that need no tool still grade correctly, and the ones
# that do refuse by name. This says why they are about to.
echo "note: no $VENV; running gates under $(command -v python3 || echo 'no python3'). Run scripts/bootstrap-worktree.sh in $ROOT for pylint, ruff and shellcheck." >&2
exec python3 "$@"
