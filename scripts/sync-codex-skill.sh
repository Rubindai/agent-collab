#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
source_dir="$repo_root/tools/agent-collab"
skill_dir="$repo_root/codex-skill/agent-collab"
target_dir="$skill_dir/tools/agent-collab"

if [[ ! -d "$source_dir" ]]; then
  echo "missing source runtime: $source_dir" >&2
  exit 1
fi

if [[ ! -f "$skill_dir/SKILL.md" ]]; then
  echo "missing Codex skill source: $skill_dir/SKILL.md" >&2
  exit 1
fi

rm -rf "$target_dir"
mkdir -p "$(dirname "$target_dir")"
cp -a "$source_dir" "$target_dir"

diff -qr "$source_dir" "$target_dir" >/dev/null
echo "synced $target_dir"
