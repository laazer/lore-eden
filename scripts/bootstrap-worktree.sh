#!/usr/bin/env bash
# Everything a fresh checkout needs before the hooks and the suites will run.
#
# Three pieces of per-checkout state that nothing restores for you, and that
# `git reset --hard` does not bring back because none of them is tracked:
#
#   python/.venv          the interpreter both pre-push commands look for
#   gates/node_modules    the TypeScript gate's parser; without it 3 tests fail
#   .git/hooks/           written by `lefthook install`, per-checkout
#
# This repo is worked in git worktrees, so a new one is created often and starts
# with none of the three. Each was learned the same way: something refused, and
# the refusal named a different problem than the one that existed.
#
# Idempotent. Safe to re-run after a reset, which is when it is most needed.
#
# Usage: bash scripts/bootstrap-worktree.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

step() { printf '\n== %s\n' "$1"; }

# --------------------------------------------------------------------------- #
# python/.venv
# --------------------------------------------------------------------------- #
#
# 3.10 is the floor, and it is checked before the venv is built rather than
# after: `python3` on a developer machine is whatever is first on PATH, and a
# venv made from a 3.9 reports its problem later, as an AttributeError out of a
# gate, which is the exact confusion `gates/lore_eden_gates/interpreter.py`
# exists to replace.
step "python/.venv"
VENV="$ROOT/python/.venv"

python_ok() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 10) else 1)' 2>/dev/null
}

if [ -x "$VENV/bin/python" ] && python_ok "$VENV/bin/python"; then
  echo "already present: $("$VENV/bin/python" -V)"
else
  if [ -e "$VENV" ]; then
    echo "replacing an existing venv that is older than 3.10" >&2
    rm -rf "$VENV"
  fi
  BASE=""
  for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && python_ok "$candidate"; then
      BASE="$candidate"
      break
    fi
  done
  if [ -z "$BASE" ]; then
    echo "no Python 3.10+ on PATH; the gates read \`match\` statements through" >&2
    echo "AST node types that do not exist before 3.10." >&2
    exit 1
  fi
  echo "creating from $("$BASE" -V)"
  "$BASE" -m venv "$VENV"
fi

# Before the editable install, not after. A 3.10 venv ships pip 21.2.4, which
# predates PEP 660 and reports the failure as `File "setup.py" or "setup.cfg"
# not found` — which reads as a packaging bug in this repo rather than as an
# out-of-date pip.
step "pip, setuptools, wheel"
"$VENV/bin/python" -m pip install --quiet --upgrade pip setuptools wheel

# `ruff` and `pylint` are not in the `dev` extra on purpose — they are what the
# two diff filters *drive*, not what the library needs — so they are installed
# beside it, exactly as .github/workflows/ci.yml does. Absent, `ruff-policy` and
# both diff filters refuse rather than reporting nothing, which is correct and
# also means every commit fails until they are here.
step "the package, its dev extra, and the linters the hooks drive"
"$VENV/bin/pip" install --quiet -e "$ROOT/python[dev]" ruff pylint

# --------------------------------------------------------------------------- #
# gates/node_modules
# --------------------------------------------------------------------------- #
#
# `npm ci`, not `npm install`: it fails on a lockfile that drifted where the
# other quietly rewrites it. Same choice the CI workflow makes.
step "gates/node_modules (the TypeScript gate's parser)"
if command -v npm >/dev/null 2>&1; then
  (cd "$ROOT/gates" && npm ci --silent)
  echo "installed"
else
  # Not fatal: the Python gates and both suites still run. The TypeScript gate
  # and 3 of its tests do not, and saying so is better than either failing here
  # or letting them skip unremarked.
  echo "npm not on PATH — skipping." >&2
  echo "The TypeScript gate cannot run without it, and 3 gate tests will skip." >&2
fi

# --------------------------------------------------------------------------- #
# .git/hooks
# --------------------------------------------------------------------------- #
step "lefthook"
if command -v lefthook >/dev/null 2>&1; then
  lefthook install
else
  echo "lefthook not on PATH — skipping." >&2
  echo "No hook will run until you install it and re-run this script." >&2
fi

printf '\n== ready\n'
echo "  pre-commit:  the 7 gate commands and the ruff policy"
echo "  pre-push:    python tests (narrowed) and the gate suite (whole)"
echo
echo "Verify with:  (cd gates && $VENV/bin/python -m pytest -q)"
