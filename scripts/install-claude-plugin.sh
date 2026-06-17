#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/install-claude-plugin.sh [--skills-root DIR | --dest DIR | --claude-home-path] [--dry-run]

Installs the packaged Claude Agent Collab plugin as a skills-directory plugin.

Options:
  --skills-root DIR    Install as DIR/agent-collab.
  --dest DIR           Install directly to DIR. DIR basename should be agent-collab.
  --claude-home-path   Install to ${CLAUDE_HOME:-$HOME/.claude}/skills/agent-collab.
  --dry-run            Validate the package and print the destination without copying.
  -h, --help           Show this help.

Default:
  Uses the Claude skills-directory plugin path: $HOME/.claude/skills/agent-collab.
USAGE
}

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
claude_home_root="${CLAUDE_HOME:-$HOME/.claude}/skills"
dest=""
dry_run=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skills-root)
      [[ $# -ge 2 ]] || { echo "--skills-root requires a directory" >&2; exit 2; }
      dest="${2%/}/agent-collab"
      shift 2
      ;;
    --dest)
      [[ $# -ge 2 ]] || { echo "--dest requires a directory" >&2; exit 2; }
      dest="${2%/}"
      shift 2
      ;;
    --claude-home-path)
      dest="$claude_home_root/agent-collab"
      shift
      ;;
    --dry-run)
      dry_run=1
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

if [[ -z "$dest" ]]; then
  dest="$claude_home_root/agent-collab"
fi

if [[ "$(basename "$dest")" != "agent-collab" ]]; then
  echo "destination basename must be agent-collab: $dest" >&2
  exit 2
fi

if [[ "$dry_run" -eq 1 ]]; then
  "$repo_root/scripts/sync-packages.sh" --claude-only --check >/dev/null
else
  "$repo_root/scripts/sync-packages.sh" --claude-only >/dev/null
fi

source_dir="$repo_root/claude-plugin/agent-collab"
if [[ ! -f "$source_dir/.claude-plugin/plugin.json" ]]; then
  echo "missing Claude plugin manifest: $source_dir/.claude-plugin/plugin.json" >&2
  exit 1
fi
if [[ ! -f "$source_dir/skills/agent-collab/SKILL.md" ]]; then
  echo "missing Claude skill source: $source_dir/skills/agent-collab/SKILL.md" >&2
  exit 1
fi

if [[ "$dry_run" -eq 1 ]]; then
  echo "$source_dir -> $dest"
  exit 0
fi

parent_dir="$(dirname "$dest")"
tmp_dir="$parent_dir/.agent-collab.tmp.$$"
mkdir -p "$parent_dir"
rm -rf "$tmp_dir"
cp -a "$source_dir" "$tmp_dir"
rm -rf "$dest"
mv "$tmp_dir" "$dest"

echo "$dest"
