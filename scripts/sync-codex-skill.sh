#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
source_dir="$repo_root/tools/agent-collab"
skill_dir="$repo_root/codex-skill/agent-collab"

if [[ ! -d "$source_dir" ]]; then
  echo "missing source runtime: $source_dir" >&2
  exit 1
fi

if [[ ! -f "$skill_dir/SKILL.md" ]]; then
  echo "missing Codex skill source: $skill_dir/SKILL.md" >&2
  exit 1
fi

rm -rf "$skill_dir/scripts" "$skill_dir/references" "$skill_dir/schemas" "$skill_dir/tools"
cp -a "$source_dir/scripts" "$skill_dir/scripts"
cp -a "$source_dir/references" "$skill_dir/references"
cp -a "$source_dir/schemas" "$skill_dir/schemas"
find "$skill_dir/scripts" -type d -name __pycache__ -prune -exec rm -rf {} +

diff -qr --exclude=__pycache__ "$source_dir/scripts" "$skill_dir/scripts" >/dev/null
diff -qr "$source_dir/references" "$skill_dir/references" >/dev/null
diff -qr "$source_dir/schemas" "$skill_dir/schemas" >/dev/null
echo "synced $skill_dir"
