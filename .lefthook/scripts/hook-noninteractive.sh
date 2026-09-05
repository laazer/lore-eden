#!/usr/bin/env bash
# Source at the top of every hook script so agent and CI runs never block on a prompt.
# shellcheck disable=SC2034
set -o pipefail 2>/dev/null || true

# Git exports GIT_DIR into hooks when pushing from a worktree — an absolute path
# to that worktree's gitdir, unset from a primary checkout. A suite that builds
# throwaway repos in tmp_path and shells out to git inherits it, and GIT_DIR beats
# cwd, so `git add .` runs against the wrong repository and exits 128. Unset it so
# git resolves through cwd, as CI does. A no-op from a primary checkout.
unset GIT_DIR GIT_WORK_TREE

export CI="${CI:-1}"
export GIT_TERMINAL_PROMPT=0
export GH_PROMPT_DISABLED=1
