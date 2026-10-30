#!/usr/bin/env bash
# No model calls or credentials: these checks also run independently of release-check.
set -euo pipefail

usage() {
  echo 'Usage: scripts/ci-provider-checks.sh {codex-skill|claude-validator|claude-install|codex-install}'
}
case "${1:-}" in
  codex-skill|claude-validator|claude-install|codex-install) check="$1" ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${PYTHON:-python3}"
work="$(mktemp -d "${TMPDIR:-/tmp}/agent-collab-provider-check.XXXXXX")"
trap 'rm -rf "$work"' EXIT
cd "$repo_root"
mkdir -p "$work/codex" "$work/claude"
# CLI-only configuration is scoped to temporary homes. No login is performed.
provider() {
  env -u OPENAI_API_KEY -u CODEX_API_KEY -u ANTHROPIC_API_KEY \
    -u ANTHROPIC_AUTH_TOKEN -u CLAUDE_CODE_OAUTH_TOKEN -u CLAUDE_PLUGIN_DATA \
    CODEX_HOME="$work/codex" CLAUDE_CONFIG_DIR="$work/claude" "$@"
}

case "$check" in
  codex-skill)
    codex_version="$(provider codex --version)"
    # The native CLI's plugin commands do not populate system skills. Retrieve
    # the official validator from that exact CLI release, never an old local
    # plugin-creator script or a copy maintained by this repository.
    "$python_bin" - "$work" "$codex_version" <<'PY'
import hashlib
import pathlib
import re
import sys
import urllib.request

work = pathlib.Path(sys.argv[1])
match = re.fullmatch(r"codex-cli (\d+\.\d+\.\d+)", sys.argv[2])
if match is None:
    raise SystemExit(f"expected a stable Codex CLI version, got {sys.argv[2]!r}")
version = match[1]
base = (f"https://raw.githubusercontent.com/openai/codex/rust-v{version}/"
        "codex-rs/skills/src/assets/samples/skill-creator/")
with urllib.request.urlopen(base + "SKILL.md", timeout=60) as response:
    instructions = response.read().decode("utf-8")
if "scripts/quick_validate.py" not in instructions:
    raise SystemExit("installed Codex release no longer documents quick_validate.py")
with urllib.request.urlopen(base + "scripts/quick_validate.py", timeout=60) as response:
    validator = response.read()
(work / "quick_validate.py").write_bytes(validator)
print(f"Official Codex {version} validator: {base}scripts/quick_validate.py")
print(f"SHA256: {hashlib.sha256(validator).hexdigest()}")
PY
    for skill in tools/agent-collab plugins/agent-collab/skills/agent-collab; do
      PYTHONDONTWRITEBYTECODE=1 "$python_bin" "$work/quick_validate.py" "$skill"
    done
    ;;
  claude-validator)
    provider claude --version
    provider claude plugin validate . --strict
    provider claude plugin validate plugins/agent-collab --strict
    ;;
  claude-install)
    provider claude --version
    provider claude plugin marketplace add ./ --scope user
    provider claude plugin install agent-collab@agent-collab --scope user
    provider claude plugin list --json > "$work/installed.json"
    ;;
  codex-install)
    provider codex --version
    provider codex plugin marketplace add .
    provider codex plugin add agent-collab@agent-collab
    provider codex plugin list --marketplace agent-collab --json > "$work/installed.json"
    ;;
esac

if [[ "$check" == *-install ]]; then
  "$python_bin" - "$check" "$work/installed.json" <<'PY'
import json
import pathlib
import sys

check, filename = sys.argv[1:]
data = json.loads(pathlib.Path(filename).read_text(encoding="utf-8"))
version = pathlib.Path("VERSION").read_text(encoding="utf-8").strip()
rows = data["installed"] if check == "codex-install" else data
key = "pluginId" if check == "codex-install" else "id"
matches = [row for row in rows if row[key] == "agent-collab@agent-collab"]
if len(matches) != 1 or matches[0].get("version") != version or matches[0].get("enabled") is not True:
    raise SystemExit(f"installed plugin must be enabled at version {version}: {matches!r}")
if check == "claude-install" and matches[0].get("scope") != "user":
    raise SystemExit("Claude installation was not saved at user scope")
print(f"Verified enabled {check} at version {version}")
PY
fi
