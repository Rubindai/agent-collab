#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/install-codex-plugin.sh [--dry-run]

Installs the packaged Codex Agent Collab plugin into the default personal plugin
location and creates or updates the default personal marketplace entry.

Options:
  --dry-run    Validate and print what would be installed without copying files.
  -h, --help   Show this help.

Default install locations:
  Plugin:      $HOME/plugins/agent-collab
  Marketplace: $HOME/.agents/plugins/marketplace.json
USAGE
}

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
plugin_name="agent-collab"
source_dir="$repo_root/codex-plugin/$plugin_name"
plugins_root="$HOME/plugins"
dest="$plugins_root/$plugin_name"
marketplace_path="$HOME/.agents/plugins/marketplace.json"
dry_run=0

while [[ $# -gt 0 ]]; do
  case "$1" in
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

if [[ "$dry_run" -eq 1 ]]; then
  "$repo_root/scripts/sync-packages.sh" --codex-only --check >/dev/null
else
  "$repo_root/scripts/sync-packages.sh" --codex-only >/dev/null
fi

manifest="$source_dir/.codex-plugin/plugin.json"
skill="$source_dir/skills/$plugin_name/SKILL.md"
if [[ ! -f "$manifest" ]]; then
  echo "missing Codex plugin manifest: $manifest" >&2
  exit 1
fi
if [[ ! -f "$skill" ]]; then
  echo "missing Codex plugin skill: $skill" >&2
  exit 1
fi

if [[ "$dry_run" -eq 1 ]]; then
  echo "$source_dir -> $dest"
  echo "marketplace: $marketplace_path"
  exit 0
fi

tmp_dir="$plugins_root/.agent-collab.tmp.$$"
mkdir -p "$plugins_root"
rm -rf "$tmp_dir"
cp -a "$source_dir" "$tmp_dir"
rm -rf "$dest"
mv "$tmp_dir" "$dest"

python - "$marketplace_path" "$plugin_name" <<'PY'
import json
import sys
from pathlib import Path

marketplace_path = Path(sys.argv[1])
plugin_name = sys.argv[2]
entry = {
    "name": plugin_name,
    "source": {
        "source": "local",
        "path": f"./plugins/{plugin_name}",
    },
    "policy": {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    },
    "category": "Productivity",
}

if marketplace_path.exists():
    data = json.loads(marketplace_path.read_text(encoding="utf-8"))
else:
    data = {
        "name": "personal",
        "interface": {
            "displayName": "Personal",
        },
        "plugins": [],
    }

if not isinstance(data, dict):
    raise SystemExit(f"marketplace root must be an object: {marketplace_path}")
data.setdefault("name", "personal")
interface = data.get("interface")
if not isinstance(interface, dict):
    interface = {}
interface.setdefault("displayName", "Personal")
data["interface"] = interface
plugins = data.get("plugins")
if not isinstance(plugins, list):
    plugins = []

replaced = False
next_plugins = []
for plugin in plugins:
    if isinstance(plugin, dict) and plugin.get("name") == plugin_name:
        next_plugins.append(entry)
        replaced = True
    else:
        next_plugins.append(plugin)
if not replaced:
    next_plugins.append(entry)
data["plugins"] = next_plugins

marketplace_path.parent.mkdir(parents=True, exist_ok=True)
marketplace_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
print(marketplace_path)
PY

echo "$dest"
