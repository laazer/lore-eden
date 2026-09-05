#!/usr/bin/env bash
# Pre-push: the python suite, narrowed to the tests the pushed commits can reach.
#
# The full suite is around a minute idle and several minutes under load, which is
# how a pre-push hook teaches people to reach for --no-verify. This repo already
# ships the tool that fixes that — gates/lore_eden_gates/select_pytest_targets.py,
# tested and documented and, until now, called by nothing. It walks the import
# graph and biases hard toward running too much: anything it cannot map, and any
# selection of zero, means the full suite.
#
# A green push therefore predicts CI rather than proving it. CI runs everything on
# every PR, so a miss costs a slower signal, never an unguarded merge.
#
# LORE_EDEN_FULL_TESTS=1 forces the full run. LORE_EDEN_TESTS_BASE=<ref> overrides
# the base, to ask what a given range would select.
#
# Lint is deliberately absent here: making policy/ruff-base.toml hold is
# lor-extract-lore-48, which has its own decision to make about where it runs.
set -euo pipefail

# shellcheck source=hook-noninteractive.sh
source "$(cd "$(dirname "$0")" && pwd)/hook-noninteractive.sh"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY_ROOT="$ROOT/python"

if [ -x "$PY_ROOT/.venv/bin/python" ] && "$PY_ROOT/.venv/bin/python" -c "import pytest" 2>/dev/null; then
  RUN="$PY_ROOT/.venv/bin/python"
else
  echo "pre-push: need python/.venv with pytest installed." >&2
  echo "Run: cd python && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
  exit 1
fi

# The selector reads `match` statements, so it needs the same floor the gates do.
"$RUN" - <<'PY' || exit 1
import sys
if sys.version_info[:2] < (3, 10):
    print(f"pre-push: python/.venv is {sys.version.split()[0]}; the selector needs 3.10+.", file=sys.stderr)
    raise SystemExit(1)
PY

BASE="${LORE_EDEN_TESTS_BASE:-}"
if [ -z "$BASE" ]; then
  BASE="$(git -C "$ROOT" rev-parse --verify --quiet "@{push}" || true)"
fi
if [ -z "$BASE" ]; then
  BASE="$(git -C "$ROOT" rev-parse --verify --quiet origin/main || true)"
fi

TARGETS=""
REASON=""
if [ -n "${LORE_EDEN_FULL_TESTS:-}" ]; then
  REASON="LORE_EDEN_FULL_TESTS is set"
elif [ -z "$BASE" ]; then
  REASON="no @{push} or origin/main to diff against"
else
  ERR_FILE="$(mktemp)"
  set +e
  TARGETS="$("$RUN" "$ROOT/gates/lore_eden_gates/select_pytest_targets.py" \
    --repo "$ROOT" --base "$BASE" --package lore_eden \
    --project-prefix python --ignore-prefix ts --ignore-prefix gates \
    --ignore-prefix policy 2>"$ERR_FILE")"
  STATUS=$?
  set -e
  if [ $STATUS -ne 0 ]; then
    TARGETS=""
    REASON="$(cat "$ERR_FILE")"
  fi
  rm -f "$ERR_FILE"
fi

cd "$PY_ROOT"

if [ -z "$TARGETS" ]; then
  echo "pre-push: full pytest run — ${REASON:-selection unavailable}"
  exec "$RUN" -m pytest -q
fi

FILES=()
while IFS= read -r target; do
  [ -n "$target" ] || continue
  rel="${target#python/}"
  if [ ! -f "$PY_ROOT/$rel" ]; then
    # pytest reports an uncollectable path as a pass, so a stale selection would
    # look exactly like a green suite. Refuse instead.
    echo "pre-push: the selector named a missing file: $target" >&2
    exit 1
  fi
  FILES+=("$rel")
done <<< "$TARGETS"

echo "pre-push: pytest on ${#FILES[@]} test file(s) reaching the pushed changes:"
printf '  %s\n' "${FILES[@]}"
exec "$RUN" -m pytest -q "${FILES[@]}"
