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

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
claude_home_root="${CLAUDE_HOME:-$HOME/.claude}/skills"
dest=""
dry_run=0
tmp_dir=""

cleanup() {
  if [[ -n "${tmp_dir:-}" && -d "$tmp_dir" ]]; then
    rm -rf "$tmp_dir"
  fi
}
trap cleanup EXIT

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

source_dir="$repo_root/claude-plugin/agent-collab"
if [[ ! -f "$source_dir/.claude-plugin/plugin.json" ]]; then
  echo "missing Claude plugin manifest: $source_dir/.claude-plugin/plugin.json" >&2
  exit 1
fi
if [[ ! -f "$source_dir/skills/agent-collab/SKILL.md" ]]; then
  echo "missing Claude skill source: $source_dir/skills/agent-collab/SKILL.md" >&2
  exit 1
fi

resolve_path() {
  python3 - "$1" <<'PY'
from pathlib import Path
import sys
print(Path(sys.argv[1]).expanduser().resolve(strict=False))
PY
}

dest_abs="$(resolve_path "$dest")"
repo_abs="$(resolve_path "$repo_root")"
source_abs="$(resolve_path "$source_dir")"
home_abs="$(resolve_path "$HOME")"
if [[ -z "$dest_abs" || "$dest_abs" == "/" || "$dest_abs" == "$home_abs" || "$dest_abs" == "$repo_abs" ]]; then
  echo "refusing unsafe destination: $dest" >&2
  exit 2
fi
if [[ "$dest_abs" == "$source_abs" || "$dest_abs" == "$source_abs"/* ]]; then
  echo "refusing to install over source package: $dest" >&2
  exit 2
fi

"$repo_root/scripts/sync-packages.sh" --claude-only --check >/dev/null

if [[ "$dry_run" -eq 1 ]]; then
  echo "$source_dir -> $dest"
  exit 0
fi

parent_dir="$(dirname "$dest")"
mkdir -p "$parent_dir"
tmp_dir="$(mktemp -d "$parent_dir/.agent-collab.tmp.XXXXXX")"
cp -a "$source_dir/." "$tmp_dir"
rm -rf \
  "$tmp_dir/skills/agent-collab/runs" \
  "$tmp_dir/skills/agent-collab/settings.local.json"
find "$tmp_dir" -type d -name __pycache__ -prune -exec rm -rf {} +
rm -rf "$dest"
mv "$tmp_dir" "$dest"
tmp_dir=""

echo "$dest"
