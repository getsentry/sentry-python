#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <name>" >&2
    exit 1
fi

POSITIONAL_ARGS=("$@")

# For simplicity, we use the same value for both the worktree name and branch name.
WORKTREE_NAME="${POSITIONAL_ARGS[0]}"
BRANCH_NAME="${POSITIONAL_ARGS[0]}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKTREE_DIR="$REPO_ROOT/.worktrees/$WORKTREE_NAME"

if [[ -d "$WORKTREE_DIR" ]]; then
    echo "Error: worktree directory already exists: $WORKTREE_DIR" >&2
    exit 1
fi

if git -C "$REPO_ROOT" branch --list "$BRANCH_NAME" | grep -q .; then
    echo "Error: branch '$BRANCH_NAME' already exists. Delete it first or choose a different name." >&2
    exit 1
fi

echo "Creating worktree '$WORKTREE_NAME' on branch '$BRANCH_NAME'..."
git -C "$REPO_ROOT" worktree add "$WORKTREE_DIR" -b "$BRANCH_NAME"

if command -v uv &>/dev/null; then
    echo "Setting up virtual environment with uv..."
    uv venv "$WORKTREE_DIR/.venv"
else
    echo "uv not found — falling back to python -m venv..."
    python -m venv "$WORKTREE_DIR/.venv"
fi

echo ""
echo "Worktree ready!"
echo "  Path:   $WORKTREE_DIR"
echo "  Branch: $BRANCH_NAME"
echo ""
echo "To start working:"
echo "  cd $WORKTREE_DIR"
echo "  source .venv/bin/activate"
