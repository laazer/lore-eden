#!/usr/bin/env bash
# Pre-push: the gate library's own suite.
#
# Not narrowed. It is around a minute, it has no import-graph selector of its own,
# and it is the code every other repository's commits are judged by — the one
# suite where running too little is the wrong trade.
#
# ## Why this does not require python/.venv
#
# It used to, and refused outright without one. But `python/.venv` is per-checkout
# and untracked, so every fresh worktree — and this repo is worked in worktrees —
# started with a pre-push that could not run at all. Worse, the thing it insisted
# on is not the thing this suite needs: `gates/tests/conftest.py` puts
# `gates/lore_eden_gates` on `sys.path` itself, and nothing under `gates/` imports
# `lore_eden`. The editable install was never a dependency of this suite; it was
# just where pytest happened to be.
#
# So: use the venv when it is there, and otherwise any interpreter that can
# actually run the suite. What that means is asked rather than assumed — 3.10+
# (the gates read `match` statements through AST node types that do not exist
# before then) and pytest importable. An interpreter failing either is not a
# fallback, it is a different way to report nothing.
#
# Refusing is still the answer when no interpreter qualifies. A pre-push that
# skips is indistinguishable from a pre-push that passed, which is the failure
# this whole library exists to prevent.
set -euo pipefail

# shellcheck source=hook-noninteractive.sh
source "$(cd "$(dirname "$0")" && pwd)/hook-noninteractive.sh"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

# Both conditions in one check, in the candidate itself: asking bash to compare
# version strings is how a 3.9.7 gets read as newer than a 3.10.1.
usable() {
  [ -x "$1" ] || command -v "$1" >/dev/null 2>&1 || return 1
  "$1" - <<'PY' >/dev/null 2>&1
import sys
if sys.version_info[:2] < (3, 10):
    raise SystemExit(1)
import pytest  # noqa: F401
PY
}

RUN=""
for candidate in "$ROOT/python/.venv/bin/python" python3 python; do
  if usable "$candidate"; then
    RUN="$candidate"
    break
  fi
done

if [ -z "$RUN" ]; then
  echo "pre-push: no interpreter here can run the gate suite." >&2
  echo "It needs Python 3.10+ with pytest — the gates read \`match\` statements" >&2
  echo "through AST node types that do not exist before 3.10." >&2
  echo "Run: bash scripts/bootstrap-worktree.sh" >&2
  exit 1
fi

# Named, not silent. A fallback that does not say it fell back is how a suite
# starts running under an interpreter nobody chose.
if [ "$RUN" != "$ROOT/python/.venv/bin/python" ]; then
  echo "pre-push: python/.venv is absent or unusable; running the gate suite under $($RUN -c 'import sys; print(sys.executable)')"
fi

cd "$ROOT/gates"
exec "$RUN" -m pytest -q
