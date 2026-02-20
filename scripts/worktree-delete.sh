#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <name>" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

for WORKTREE_NAME in "$@"; do
    WORKTREE_DIR="$REPO_ROOT/.worktrees/$WORKTREE_NAME"

    if [[ ! -d "$WORKTREE_DIR" ]]; then
        echo "Warning: worktree directory not found: $WORKTREE_DIR — skipping" >&2
        continue
    fi

    # Capture branch name before removal
    BRANCH_NAME="$(git -C "$WORKTREE_DIR" branch --show-current 2>/dev/null || true)"

    echo "Removing worktree '$WORKTREE_NAME'..."
    git -C "$REPO_ROOT" worktree remove "$WORKTREE_DIR"
    echo "  Removed: $WORKTREE_DIR"

    if [[ -n "$BRANCH_NAME" ]]; then
        if [[ -t 0 ]]; then
            read -r -p "  Delete branch '$BRANCH_NAME'? [y/N] " REPLY
            echo
        else
            REPLY="n"
        fi
        if [[ "$REPLY" == "y" || "$REPLY" == "Y" ]]; then
            git -C "$REPO_ROOT" branch -d "$BRANCH_NAME"
            echo "  Deleted branch: $BRANCH_NAME"
        fi
    fi

    echo "  Done."
done
