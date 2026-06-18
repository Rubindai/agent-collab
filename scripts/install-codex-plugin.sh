#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/install-codex-plugin.sh [--dry-run] [--skip-codex-refresh]

Installs the packaged Codex Agent Collab plugin into the default personal plugin
location, creates or updates the default personal marketplace entry, and refreshes
Codex's installed plugin cache when the Codex CLI is available.

Options:
  --dry-run             Validate and print what would be installed without copying files.
  --skip-codex-refresh  Do not run `codex plugin add agent-collab@personal --json`.
  -h, --help            Show this help.

Default install locations:
  Plugin:      $HOME/plugins/agent-collab
  Marketplace: $HOME/.agents/plugins/marketplace.json
USAGE
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
plugin_name="agent-collab"
source_dir="$repo_root/codex-plugin/$plugin_name"
plugins_root="$HOME/plugins"
dest="$plugins_root/$plugin_name"
marketplace_path="$HOME/.agents/plugins/marketplace.json"
dry_run=0
skip_codex_refresh=0
codex_selector="$plugin_name@personal"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      dry_run=1
      shift
      ;;
    --skip-codex-refresh)
      skip_codex_refresh=1
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

"$repo_root/scripts/sync-packages.sh" --codex-only --check >/dev/null

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

python_bin="${PYTHON:-}"
if [[ -n "$python_bin" ]]; then
  if ! command -v "$python_bin" >/dev/null 2>&1; then
    echo "configured PYTHON is not executable: $python_bin" >&2
    exit 1
  fi
else
  python_bin="$(command -v python3 || command -v python || true)"
  if [[ -z "$python_bin" ]]; then
    echo "python3 or python is required to update the personal marketplace" >&2
    exit 1
  fi
fi

if [[ "$dry_run" -eq 1 ]]; then
  echo "$source_dir -> $dest"
  echo "marketplace: $marketplace_path"
  if [[ "$skip_codex_refresh" -eq 1 ]]; then
    echo "codex refresh: skipped"
  else
    echo "codex refresh: codex plugin add $codex_selector --json"
  fi
  exit 0
fi

tmp_dir="$plugins_root/.agent-collab.tmp.$$"
marketplace_tmp="$marketplace_path.tmp.$$"
trap 'rm -rf "$tmp_dir" "$marketplace_tmp"' EXIT
mkdir -p "$plugins_root"
rm -rf "$tmp_dir"

"$python_bin" - "$marketplace_path" "$marketplace_tmp" "$plugin_name" <<'PY'
import json
import sys
from pathlib import Path

marketplace_path = Path(sys.argv[1])
marketplace_tmp = Path(sys.argv[2])
plugin_name = sys.argv[3]
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
marketplace_tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
print(marketplace_path)
PY

cp -a "$source_dir" "$tmp_dir"
rm -rf \
  "$tmp_dir/skills/$plugin_name/runs" \
  "$tmp_dir/skills/$plugin_name/settings.local.json"
find "$tmp_dir" -type d -name __pycache__ -prune -exec rm -rf {} +
rm -rf "$dest"
mv "$tmp_dir" "$dest"
mv "$marketplace_tmp" "$marketplace_path"

echo "$dest"

if [[ "$skip_codex_refresh" -eq 1 ]]; then
  echo "codex refresh skipped; run 'codex plugin add $codex_selector --json' to refresh the installed cache."
elif command -v codex >/dev/null 2>&1; then
  echo "refreshing Codex plugin cache: codex plugin add $codex_selector --json"
  codex plugin add "$codex_selector" --json
else
  echo "warning: Codex CLI not found; run 'codex plugin add $codex_selector --json' to refresh the installed cache." >&2
fi
