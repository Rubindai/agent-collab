#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/install-codex-skill.sh [--skills-root DIR | --dest DIR | --docs-path | --codex-home-path]

Installs the packaged Codex Agent Collab skill globally.

Options:
  --skills-root DIR   Install as DIR/agent-collab.
  --dest DIR          Install directly to DIR. DIR basename should be agent-collab.
  --docs-path         Install to the latest documented user path: $HOME/.agents/skills/agent-collab.
  --codex-home-path   Install to the current Codex-home path: ${CODEX_HOME:-$HOME/.codex}/skills/agent-collab.
  -h, --help          Show this help.

Default:
  Uses ${CODEX_HOME:-$HOME/.codex}/skills/agent-collab when that skills root exists,
  otherwise uses $HOME/.agents/skills/agent-collab.
USAGE
}

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
codex_home_root="${CODEX_HOME:-$HOME/.codex}/skills"
docs_root="$HOME/.agents/skills"
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
    --docs-path)
      dest="$docs_root/agent-collab"
      shift
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
  if [[ -d "$codex_home_root" ]]; then
    dest="$codex_home_root/agent-collab"
  else
    dest="$docs_root/agent-collab"
  fi
fi

"$repo_root/scripts/sync-codex-skill.sh" >/dev/null

source_dir="$repo_root/codex-skill/agent-collab"
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

if [[ -d "$repo_root/.agents/skills/agent-collab" ]]; then
  echo "warning: repo-local Codex skill still exists at .agents/skills/agent-collab" >&2
fi

echo "$dest"
