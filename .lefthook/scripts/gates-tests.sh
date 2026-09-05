#!/usr/bin/env bash
# Pre-push: the gate library's own suite.
#
# Not narrowed. It is around a minute, it has no import-graph selector of its own,
# and it is the code every other repository's commits are judged by — the one
# suite where running too little is the wrong trade.
set -euo pipefail

# shellcheck source=hook-noninteractive.sh
source "$(cd "$(dirname "$0")" && pwd)/hook-noninteractive.sh"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

if [ -x "$ROOT/python/.venv/bin/python" ]; then
  RUN="$ROOT/python/.venv/bin/python"
else
  echo "pre-push: need python/.venv to run the gate suite." >&2
  exit 1
fi

cd "$ROOT/gates"
exec "$RUN" -m pytest -q
