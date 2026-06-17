#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/sync-packages.sh [--check] [--codex-only | --claude-only]

Sync shared Agent Collab runtime resources into packaged skill roots.

Options:
  --check        Verify packages are already synced without changing files.
  --codex-only   Sync or check only the Codex plugin skill.
  --claude-only  Sync or check only the Claude plugin skill.
  -h, --help     Show this help.
USAGE
}

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
source_dir="$repo_root/tools/agent-collab"
check=0
mode="all"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)
      check=1
      shift
      ;;
    --codex-only)
      mode="codex"
      shift
      ;;
    --claude-only)
      mode="claude"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! -d "$source_dir" ]]; then
  echo "missing source runtime: $source_dir" >&2
  exit 1
fi

sync_package() {
  local package_dir="$1"
  local label="$2"
  local package_root="$3"

  if [[ ! -d "$package_dir" ]]; then
    echo "missing $label skill root: $package_dir" >&2
    exit 1
  fi

  if [[ "$check" -eq 0 ]]; then
    rm -rf "$package_dir/scripts" "$package_dir/references" "$package_dir/schemas" "$package_dir/tools"
    cp -a "$source_dir/scripts" "$package_dir/scripts"
    cp -a "$source_dir/references" "$package_dir/references"
    cp -a "$source_dir/schemas" "$package_dir/schemas"
    find "$package_dir/scripts" -type d -name __pycache__ -prune -exec rm -rf {} +
  fi

  diff -qr --exclude=__pycache__ "$source_dir/scripts" "$package_dir/scripts" >/dev/null
  diff -qr "$source_dir/references" "$package_dir/references" >/dev/null
  diff -qr "$source_dir/schemas" "$package_dir/schemas" >/dev/null
  if [[ -d "$package_dir/tools" ]]; then
    echo "$label skill must not contain nested tools/: $package_dir/tools" >&2
    exit 1
  fi
  if [[ -n "$package_root" ]]; then
    for dirname in scripts references schemas; do
      if [[ -e "$package_root/$dirname" ]]; then
        echo "$label package root must not contain $dirname/: $package_root/$dirname" >&2
        exit 1
      fi
    done
    if [[ -e "$package_root/tools" ]]; then
      echo "$label package root must not contain tools/: $package_root/tools" >&2
      exit 1
    fi
  fi
}

codex_skill_dir="$repo_root/codex-plugin/agent-collab/skills/agent-collab"
claude_plugin_root="$repo_root/claude-plugin/agent-collab"
claude_skill_dir="$claude_plugin_root/skills/agent-collab"

if [[ -e "$repo_root/codex-skill" ]]; then
  echo "legacy codex-skill/ must not exist in this repo" >&2
  exit 1
fi
if [[ -e "$repo_root/.codex" || -e "$repo_root/.claude" || -e "$repo_root/.agents" ]]; then
  echo "active host config directories must not be part of the repo tree" >&2
  exit 1
fi

for manifest in \
  "$repo_root/codex-plugin/agent-collab/.codex-plugin/plugin.json" \
  "$claude_plugin_root/.claude-plugin/plugin.json"
do
  if [[ ! -f "$manifest" ]]; then
    echo "missing plugin manifest: $manifest" >&2
    exit 1
  fi
done

case "$mode" in
  all)
    sync_package "$codex_skill_dir" "Codex plugin" "$repo_root/codex-plugin/agent-collab"
    sync_package "$claude_skill_dir" "Claude plugin" "$claude_plugin_root"
    ;;
  codex)
    sync_package "$codex_skill_dir" "Codex plugin" "$repo_root/codex-plugin/agent-collab"
    ;;
  claude)
    sync_package "$claude_skill_dir" "Claude plugin" "$claude_plugin_root"
    ;;
esac

if [[ "$check" -eq 1 ]]; then
  echo "packages are synced"
else
  echo "synced packages"
fi
