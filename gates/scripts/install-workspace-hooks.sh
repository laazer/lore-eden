#!/usr/bin/env bash
# Install lore-eden's shared gates into a repository's pre-commit hooks.
#
# The orchestration gates already run these against a workspace during an
# agent run. This covers the other half: commits a human makes by hand, which
# no orchestration gate ever sees.
#
# The installed entries *reference* this checkout's gate scripts by absolute
# path rather than copying them in. A copy in each repo is a copy that drifts,
# and these rules are meant to be one thing — three drifted forks of the same
# organization gate is the reason this library exists.
#
# Usage:
#   scripts/install-workspace-hooks.sh /path/to/repo [...]
#   scripts/install-workspace-hooks.sh --check /path/to/repo   # report only
#
# Idempotent: the block is delimited by markers and rewritten in place. A block
# written by loregarden's predecessor of this script is replaced, not stacked.

set -euo pipefail

GATES_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/lore_eden_gates"

check_only=0
targets=()
for arg in "$@"; do
  case "$arg" in
    --check) check_only=1 ;;
    *) targets+=("$arg") ;;
  esac
done

if [ ${#targets[@]} -eq 0 ]; then
  echo "usage: $0 [--check] <repo-root> [...]" >&2
  exit 2
fi

status=0
for target in "${targets[@]}"; do
  if [ ! -d "$target/.git" ]; then
    echo "skip: $target is not a git repository" >&2
    status=1
    continue
  fi
  if [ ! -f "$target/lefthook.yml" ]; then
    echo "skip: $target has no lefthook.yml (install lefthook there first)" >&2
    status=1
    continue
  fi
  if ! python3 "$GATES_ROOT/install_workspace_hooks.py" \
    --config "$target/lefthook.yml" \
    --gates-root "$GATES_ROOT" \
    ${check_only:+$([ "$check_only" -eq 1 ] && echo --check)}; then
    status=1
  fi
done

exit "$status"
