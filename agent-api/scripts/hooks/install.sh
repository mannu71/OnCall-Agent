#!/usr/bin/env bash
# Install the repo's git hooks (currently: pre-push prompt gate).
set -euo pipefail
repo_root="$(git rev-parse --show-toplevel)"
hooks_dir="$repo_root/.git/hooks"
src_dir="$repo_root/agent-api/scripts/hooks"

cp "$src_dir/pre-push" "$hooks_dir/pre-push"
chmod +x "$hooks_dir/pre-push"
echo "Installed pre-push prompt gate -> $hooks_dir/pre-push"
echo "Bypass a single push with: PROMPT_GATE_SKIP=1 git push"
