#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/sync-packages.sh [--check]

Sync shared Agent Collab runtime, documentation, license, and branding assets into the unified dual-host package.

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

python_bin="${PYTHON:-python3}"
if ! command -v "$python_bin" >/dev/null 2>&1; then
  echo "Python 3 is required; set PYTHON or install python3" >&2
  exit 1
fi

# Keep the provider projection generated from the richer canonical contract.
if [[ "$check" == 1 ]]; then
  "$python_bin" "$source_dir/scripts/provider_schema.py" --check
else
  "$python_bin" "$source_dir/scripts/provider_schema.py"
fi

plugin_root="$repo_root/plugins/agent-collab"
skill_dir="$plugin_root/skills/agent-collab"
shared_files=(SKILL.md)
shared_directories=(scripts references schemas)
claude_agents_source="$source_dir/agents/claude"
codex_agent_source="$source_dir/agents/codex/openai.yaml"

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
if [[ ! -d "$claude_agents_source" || ! -f "$codex_agent_source" ]]; then
  echo "missing canonical host agent assets under $source_dir/agents" >&2
  exit 1
fi
if [[ ! -f "$repo_root/LICENSE" || ! -f "$source_dir/README.md" || ! -d "$source_dir/assets" ]]; then
  echo "missing package license, README, or canonical branding assets" >&2
  exit 1
fi

# New source-root entries must be classified explicitly so shipped payloads
# cannot be omitted silently. Runtime state and caches remain source-local.
while IFS= read -r source_entry; do
  source_name="${source_entry##*/}"
  case "$source_name" in
    SKILL.md|README.md|assets|agents|scripts|references|schemas|evals|runs|settings.local.json|__pycache__)
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
  "$plugin_root/plugin.json" \
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


"$python_bin" - "$repo_root" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
expected_source = "./plugins/agent-collab"


def strict_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise SystemExit(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def reject_constant(token):
    raise SystemExit(f"non-finite JSON number {token}")


def load(relative):
    try:
        text = (root / relative).read_text(encoding="utf-8")
        return json.loads(text, object_pairs_hook=strict_object, parse_constant=reject_constant)
    except (OSError, UnicodeError, ValueError) as exc:
        raise SystemExit(f"{relative} is not strict JSON: {exc}")


def plugin_entry(marketplace, relative):
    plugins = marketplace.get("plugins") if isinstance(marketplace, dict) else None
    if not isinstance(plugins, list):
        raise SystemExit(f"{relative} must contain a plugins array")
    matches = [entry for entry in plugins if isinstance(entry, dict) and entry.get("name") == "agent-collab"]
    if len(matches) != 1:
        raise SystemExit(f"{relative} must list agent-collab exactly once")
    return matches[0]


codex_entry = plugin_entry(load(".agents/plugins/marketplace.json"), ".agents/plugins/marketplace.json")
claude_entry = plugin_entry(load(".claude-plugin/marketplace.json"), ".claude-plugin/marketplace.json")
codex_source = codex_entry.get("source")
codex_path = codex_source.get("path") if isinstance(codex_source, dict) else None
if codex_path != expected_source or claude_entry.get("source") != expected_source:
    raise SystemExit("both marketplaces must point to ./plugins/agent-collab")

version = (root / "VERSION").read_text(encoding="utf-8").strip()
for relative_manifest in (
    "plugins/agent-collab/plugin.json",
    "plugins/agent-collab/.claude-plugin/plugin.json",
):
    manifest = load(relative_manifest)
    if not isinstance(manifest, dict) or manifest.get("name") != "agent-collab" or manifest.get("version") != version:
        raise SystemExit(f"manifest name/version does not match agent-collab {version}: {relative_manifest}")
PY

staging_dir="$(mktemp -d "${TMPDIR:-/tmp}/agent-collab-sync.XXXXXX")"
new_skill_dir=""
new_agents_dir=""
new_metadata_dir=""
cleanup() {
  rm -rf "$staging_dir"
  [[ -n "$new_skill_dir" ]] && rm -rf "$new_skill_dir"
  [[ -n "$new_agents_dir" ]] && rm -rf "$new_agents_dir"
  [[ -n "$new_metadata_dir" ]] && rm -rf "$new_metadata_dir"
  return 0
}
trap cleanup EXIT

for filename in "${shared_files[@]}"; do
  cp -a "$source_dir/$filename" "$staging_dir/$filename"
done
for dirname in "${shared_directories[@]}"; do
  cp -a "$source_dir/$dirname" "$staging_dir/$dirname"
done
cp -a "$claude_agents_source" "$staging_dir/claude-agents"
mkdir -p "$staging_dir/codex-agents"
cp -a "$codex_agent_source" "$staging_dir/codex-agents/openai.yaml"
mkdir -p "$staging_dir/package"
cp -a "$repo_root/LICENSE" "$source_dir/README.md" "$source_dir/assets" "$source_dir/evals" "$staging_dir/package/"
rm -rf "$staging_dir/package/evals/results"
find "$staging_dir" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$staging_dir" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

if [[ "$check" -eq 0 ]]; then
  # Build the complete generated trees next to their destinations, then swap
  # them in with renames so an interrupted sync never leaves a partial package.
  # Anything unclassified in the old package is dropped with it.
  skills_parent="$(dirname "$skill_dir")"
  find "$skills_parent" -mindepth 1 -maxdepth 1 -name '.agent-collab.*' -exec rm -rf {} +
  find "$plugin_root" -mindepth 1 -maxdepth 1 -name '.agents.*' -exec rm -rf {} +
  find "$plugin_root" -mindepth 1 -maxdepth 1 -name '.metadata.*' -exec rm -rf {} +
  find "$plugin_root" -mindepth 1 -maxdepth 1 \( -name '.assets.old.*' -o -name '.evals.old.*' \) -exec rm -rf {} +
  new_skill_dir="$(mktemp -d "$skills_parent/.agent-collab.sync.XXXXXX")"
  new_agents_dir="$(mktemp -d "$plugin_root/.agents.sync.XXXXXX")"
  new_metadata_dir="$(mktemp -d "$plugin_root/.metadata.sync.XXXXXX")"
  chmod 0755 "$new_skill_dir" "$new_agents_dir"
  for filename in "${shared_files[@]}"; do
    cp -a "$staging_dir/$filename" "$new_skill_dir/$filename"
  done
  for dirname in "${shared_directories[@]}"; do
    cp -a "$staging_dir/$dirname" "$new_skill_dir/$dirname"
  done
  cp -a "$staging_dir/codex-agents" "$new_skill_dir/agents"
  cp -a "$staging_dir/claude-agents/." "$new_agents_dir/"
  cp -a "$staging_dir/package/." "$new_metadata_dir/"
  old_skill_dir="$skills_parent/.agent-collab.old.$$"
  old_agents_dir="$plugin_root/.agents.old.$$"
  mv "$skill_dir" "$old_skill_dir"
  mv "$new_skill_dir" "$skill_dir"
  new_skill_dir=""
  if [[ -e "$plugin_root/agents" ]]; then
    mv "$plugin_root/agents" "$old_agents_dir"
  fi
  mv "$new_agents_dir" "$plugin_root/agents"
  new_agents_dir=""
  for filename in LICENSE README.md; do
    mv "$new_metadata_dir/$filename" "$plugin_root/$filename"
  done
  for dirname in assets evals; do
    old_metadata_dir="$plugin_root/.$dirname.old.$$"
    if [[ -e "$plugin_root/$dirname" ]]; then
      mv "$plugin_root/$dirname" "$old_metadata_dir"
    fi
    mv "$new_metadata_dir/$dirname" "$plugin_root/$dirname"
    rm -rf "$old_metadata_dir"
  done
  rm -rf "$new_metadata_dir"
  new_metadata_dir=""
  rm -rf "$old_skill_dir" "$old_agents_dir"
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

# Compare bytes, symlink targets, and permission bits; diff -r ignores modes.
"$python_bin" - "$staging_dir" "$skill_dir" "$plugin_root" "${shared_files[@]}" -- "${shared_directories[@]}" <<'PY'
import os
import pathlib
import stat
import sys

staging, skill, plugin = (pathlib.Path(item) for item in sys.argv[1:4])
separator = sys.argv.index("--")
files, directories = sys.argv[4:separator], sys.argv[separator + 1:]


def signature(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        return ("link", os.readlink(path))
    mode = stat.S_IMODE(info.st_mode)
    if stat.S_ISDIR(info.st_mode):
        return ("dir", mode)
    return ("file", mode, path.read_bytes())


def tree(root):
    if not root.exists():
        return None
    return {str(item.relative_to(root)): signature(item) for item in sorted(root.rglob("*"))}


pairs = [(staging / name, skill / name, f"packaged {name}") for name in files]
pairs += [(staging / name, skill / name, f"packaged {name}") for name in directories]
pairs += [
    (staging / "claude-agents", plugin / "agents", "packaged Claude agents"),
    (staging / "codex-agents", skill / "agents", "packaged Codex agent metadata"),
]
pairs += [(staging / "package" / name, plugin / name, f"packaged {name}") for name in ("LICENSE", "README.md", "assets", "evals")]
for source, packaged, label in pairs:
    same = (
        signature(source) == signature(packaged) if source.is_file() and packaged.is_file()
        else tree(source) == tree(packaged)
    )
    if not same:
        raise SystemExit(f"{label} is not synchronized with its canonical source (content or file mode differs)")
PY

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
