#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/sync-packages.sh [--check]

Sync shared Agent Collab runtime resources into the unified dual-host package.

Options:
  --check      Verify the package is already synced without changing files.
  -h, --help   Show this help.
USAGE
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
source_dir="$repo_root/tools/agent-collab"
check=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)
      check=1
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

plugin_root="$repo_root/plugins/agent-collab"
skill_dir="$plugin_root/skills/agent-collab"
shared_files=(SKILL.md)
shared_directories=(scripts references schemas)

if [[ ! -d "$skill_dir" ]]; then
  echo "missing unified plugin skill root: $skill_dir" >&2
  exit 1
fi

for filename in "${shared_files[@]}"; do
  if [[ ! -f "$source_dir/$filename" ]]; then
    echo "missing shared source file: $source_dir/$filename" >&2
    exit 1
  fi
done
for dirname in "${shared_directories[@]}"; do
  if [[ ! -d "$source_dir/$dirname" ]]; then
    echo "missing shared source directory: $source_dir/$dirname" >&2
    exit 1
  fi
done

# New source-root entries must be classified explicitly so shipped payloads
# cannot be omitted silently. Runtime state and caches remain source-local.
while IFS= read -r source_entry; do
  source_name="${source_entry##*/}"
  case "$source_name" in
    SKILL.md|scripts|references|schemas|runs|settings.local.json|__pycache__)
      ;;
    *)
      echo "unclassified Agent Collab source entry: $source_entry" >&2
      exit 1
      ;;
  esac
done < <(find "$source_dir" -mindepth 1 -maxdepth 1 -print)

for removed_path in \
  codex-skill \
  codex-plugin \
  claude-plugin \
  plugins/claude \
  scripts/install-claude-plugin.sh \
  scripts/install-codex-plugin.sh \
  scripts/install-codex-skill.sh \
  scripts/sync-codex-skill.sh
do
  if [[ -e "$repo_root/$removed_path" ]]; then
    echo "removed package path must not exist: $repo_root/$removed_path" >&2
    exit 1
  fi
done
if [[ -e "$repo_root/.codex" || -e "$repo_root/.claude" ]]; then
  echo "active host config directories must not be part of the repo tree" >&2
  exit 1
fi
if [[ -d "$repo_root/.agents" ]] && find "$repo_root/.agents" -type f ! -path "$repo_root/.agents/plugins/marketplace.json" -print -quit | grep -q .; then
  echo ".agents may contain only plugins/marketplace.json" >&2
  exit 1
fi

for manifest in \
  "$plugin_root/.codex-plugin/plugin.json" \
  "$plugin_root/.claude-plugin/plugin.json"
do
  if [[ ! -f "$manifest" ]]; then
    echo "missing plugin manifest: $manifest" >&2
    exit 1
  fi
done

for required_path in \
  "$plugin_root/agents" \
  "$skill_dir/agents/openai.yaml"
do
  if [[ ! -e "$required_path" ]]; then
    echo "missing dual-host package component: $required_path" >&2
    exit 1
  fi
done

python - "$repo_root" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
expected_source = "./plugins/agent-collab"

with (root / ".agents/plugins/marketplace.json").open(encoding="utf-8") as handle:
    codex_marketplace = json.load(handle)
with (root / ".claude-plugin/marketplace.json").open(encoding="utf-8") as handle:
    claude_marketplace = json.load(handle)

codex_entry = next(
    (entry for entry in codex_marketplace.get("plugins", []) if entry.get("name") == "agent-collab"),
    None,
)
claude_entry = next(
    (entry for entry in claude_marketplace.get("plugins", []) if entry.get("name") == "agent-collab"),
    None,
)
codex_source = (codex_entry or {}).get("source", {}).get("path")
claude_source = (claude_entry or {}).get("source")
if codex_source != expected_source or claude_source != expected_source:
    raise SystemExit("both marketplaces must point to ./plugins/agent-collab")

version = (root / "VERSION").read_text(encoding="utf-8").strip()
for relative_manifest in (
    "plugins/agent-collab/.codex-plugin/plugin.json",
    "plugins/agent-collab/.claude-plugin/plugin.json",
):
    with (root / relative_manifest).open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    if manifest.get("name") != "agent-collab" or manifest.get("version") != version:
        raise SystemExit(f"manifest name/version does not match agent-collab {version}: {relative_manifest}")
PY

staging_dir="$(mktemp -d "${TMPDIR:-/tmp}/agent-collab-sync.XXXXXX")"
cleanup() {
  rm -rf "$staging_dir"
}
trap cleanup EXIT

for filename in "${shared_files[@]}"; do
  cp -a "$source_dir/$filename" "$staging_dir/$filename"
done
for dirname in "${shared_directories[@]}"; do
  cp -a "$source_dir/$dirname" "$staging_dir/$dirname"
done
find "$staging_dir" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$staging_dir" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

if [[ "$check" -eq 0 ]]; then
  # This directory is generated entirely from the canonical source tree. Drop
  # any stale pre-1.0 or otherwise unclassified payload before copying.
  while IFS= read -r -d '' packaged_entry; do
    packaged_name="${packaged_entry##*/}"
    case "$packaged_name" in
      SKILL.md|agents|scripts|references|schemas)
        ;;
      *)
        rm -rf -- "$packaged_entry"
        ;;
    esac
  done < <(find "$skill_dir" -mindepth 1 -maxdepth 1 -print0)
  for filename in "${shared_files[@]}"; do
    rm -f "$skill_dir/$filename"
    cp -a "$staging_dir/$filename" "$skill_dir/$filename"
  done
  for dirname in "${shared_directories[@]}"; do
    rm -rf "$skill_dir/$dirname"
    cp -a "$staging_dir/$dirname" "$skill_dir/$dirname"
  done
  rm -rf \
    "$skill_dir/tools" \
    "$skill_dir/runs" \
    "$skill_dir/settings.local.json"
fi

while IFS= read -r -d '' packaged_entry; do
  packaged_name="${packaged_entry##*/}"
  case "$packaged_name" in
    SKILL.md|agents|scripts|references|schemas)
      ;;
    *)
      echo "unexpected packaged skill entry: $packaged_entry" >&2
      exit 1
      ;;
  esac
done < <(find "$skill_dir" -mindepth 1 -maxdepth 1 -print0)

for filename in "${shared_files[@]}"; do
  if ! cmp -s "$staging_dir/$filename" "$skill_dir/$filename"; then
    echo "packaged $filename is not synchronized with tools/agent-collab/$filename" >&2
    exit 1
  fi
done
for dirname in "${shared_directories[@]}"; do
  if ! diff -qr "$staging_dir/$dirname" "$skill_dir/$dirname" >/dev/null; then
    echo "packaged $dirname is not synchronized with tools/agent-collab/$dirname" >&2
    exit 1
  fi
done

if [[ -e "$skill_dir/runs" || -e "$skill_dir/settings.local.json" ]]; then
  echo "packaged skill must not contain runtime state: $skill_dir" >&2
  exit 1
fi
if [[ -e "$skill_dir/tools" ]]; then
  echo "packaged skill must not contain nested tools/: $skill_dir/tools" >&2
  exit 1
fi
if find "$skill_dir" -type d -name __pycache__ -print -quit | grep -q .; then
  echo "packaged skill must not contain __pycache__ directories: $skill_dir" >&2
  exit 1
fi
if find "$skill_dir" -type f \( -name '*.pyc' -o -name '*.pyo' \) -print -quit | grep -q .; then
  echo "packaged skill must not contain Python bytecode: $skill_dir" >&2
  exit 1
fi
for dirname in "${shared_directories[@]}" tools; do
  if [[ -e "$plugin_root/$dirname" ]]; then
    echo "plugin root must not contain $dirname/: $plugin_root/$dirname" >&2
    exit 1
  fi
done

if [[ "$check" -eq 1 ]]; then
  echo "package is synced"
else
  echo "synced package"
fi
