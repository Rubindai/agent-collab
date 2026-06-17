#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
exec "$repo_root/scripts/sync-packages.sh" --codex-only "$@"
