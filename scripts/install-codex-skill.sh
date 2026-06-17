#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/install-codex-skill.sh [--skills-root DIR | --dest DIR | --codex-home-path]

Installs the Codex Agent Collab skill from the packaged Codex plugin.

Options:
  --skills-root DIR   Install as DIR/agent-collab.
  --dest DIR          Install directly to DIR. DIR basename should be agent-collab.
  --codex-home-path   Install to the current Codex-home path: ${CODEX_HOME:-$HOME/.codex}/skills/agent-collab.
  -h, --help          Show this help.

Default:
  Uses the documented user skill path: $HOME/.agents/skills/agent-collab.
USAGE
}

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
docs_root="$HOME/.agents/skills"
codex_home_root="${CODEX_HOME:-$HOME/.codex}/skills"
dest=""

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
    --codex-home-path)
      dest="$codex_home_root/agent-collab"
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
  dest="$docs_root/agent-collab"
fi

"$repo_root/scripts/sync-packages.sh" --codex-only >/dev/null

source_dir="$repo_root/codex-plugin/agent-collab/skills/agent-collab"
if [[ ! -f "$source_dir/SKILL.md" ]]; then
  echo "missing Codex skill source: $source_dir/SKILL.md" >&2
  exit 1
fi

if [[ "$(basename "$dest")" != "agent-collab" ]]; then
  echo "destination basename must be agent-collab: $dest" >&2
  exit 2
fi

parent_dir="$(dirname "$dest")"
tmp_dir="$parent_dir/.agent-collab.tmp.$$"
mkdir -p "$parent_dir"
rm -rf "$tmp_dir"
cp -a "$source_dir" "$tmp_dir"
rm -rf "$dest"
mv "$tmp_dir" "$dest"

echo "$dest"
