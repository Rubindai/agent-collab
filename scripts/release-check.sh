#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/release-check.sh [--ci]

Run every Agent Collab release verification step in order.

  (default)  Release mode: every step is required. A step whose tool is
             unavailable (Codex skill validator, claude, codex, jsonschema)
             fails the check instead of being skipped.
  --ci       Continuous integration: the same required checks, including
             no-auth provider validation and isolated marketplace installs;
             platform-dependent unit tests may report skips.
  -h, --help Show this help.
USAGE
}

ci=0
case "${1:-}" in
  "") ;;
  --ci) ci=1 ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
python_bin="${PYTHON:-python3}"
work="$(mktemp -d "${TMPDIR:-/tmp}/agent-collab-release-check.XXXXXX")"
trap 'rm -rf "$work"' EXIT
cd "$repo_root"

failures=0
step() { printf '\n== %s\n' "$1"; }
fail() { echo "FAILED: $1" >&2; failures=$((failures + 1)); }

step "unit tests"
# On failure print every failure and error block, not just the summary, so a
# CI log identifies each failing test.
show_test_failures() {
  if grep -q '^======================================================================$' "$work/tests.log"; then
    sed -n '/^======================================================================$/,$p' "$work/tests.log" | head -1000 >&2
  else
    tail -40 "$work/tests.log" >&2
  fi
}
PYTHONDONTWRITEBYTECODE=1 "$python_bin" -m unittest discover -s tests -v >"$work/tests.log" 2>&1 \
  || { show_test_failures; fail "unit tests"; }
tail -3 "$work/tests.log"
if grep -Eq '^OK \(skipped=|skipped [0-9]+' "$work/tests.log"; then
  grep -E "skipped '" "$work/tests.log" || true
  if [[ "$ci" -eq 1 ]]; then
    echo "Platform-dependent unit test skips reported in CI mode (see above)"
  else
    fail "unit tests reported skips"
  fi
fi

step "package synchronization"
scripts/sync-packages.sh --check || fail "package synchronization"

step "Python compilation"
PYTHONPYCACHEPREFIX="$work/pycache" "$python_bin" -m py_compile tools/agent-collab/scripts/*.py \
  || fail "Python compilation"

step "strict JSON and schema meta-validation"
"$python_bin" - <<'PY' || fail "strict JSON and schema meta-validation"
import json
import pathlib
import subprocess
import sys

def strict(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate key {key!r}")
        value[key] = item
    return value

def constant(token):
    raise ValueError(f"non-finite number {token}")

# Validate the candidate working tree, including new manifests before staging
# and excluding tracked paths removed by the change under review.
names = subprocess.check_output(
    ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "*.json"],
    text=True,
).split("\0")
files = sorted({name for name in names if name and pathlib.Path(name).is_file()})
documents = {}
for name in files:
    with open(name, encoding="utf-8") as handle:
        documents[name] = json.loads(handle.read(), object_pairs_hook=strict, parse_constant=constant)
print(f"strictly parsed {len(files)} JSON files")
try:
    from jsonschema.validators import validator_for
except ImportError:
    sys.exit("jsonschema is required for schema meta-validation (pip install jsonschema)")
schemas = [name for name in files if name.endswith(".schema.json")]
for name in schemas:
    validator_for(documents[name]).check_schema(documents[name])
print(f"meta-validated {len(schemas)} schemas")
PY

step "release version consistency"
"$python_bin" - <<'PY' || fail "release version consistency"
import json
import pathlib
import re

version = pathlib.Path("VERSION").read_text(encoding="utf-8").strip()
if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version):
    raise SystemExit(f"VERSION is not Semantic Versioning 2.0.0: {version!r}")
for manifest in ("plugins/agent-collab/plugin.json", "plugins/agent-collab/.claude-plugin/plugin.json"):
    found = json.loads(pathlib.Path(manifest).read_text(encoding="utf-8"))["version"]
    if found != version:
        raise SystemExit(f"{manifest} version {found} != VERSION {version}")
if f"## {version} " not in pathlib.Path("CHANGELOG.md").read_text(encoding="utf-8"):
    raise SystemExit(f"CHANGELOG.md has no entry for {version}")
print(f"version {version} matches both manifests and the changelog")
PY

step "official Codex skill validator"
scripts/ci-provider-checks.sh codex-skill || fail "official Codex skill validator"

step "Claude plugin validator"
scripts/ci-provider-checks.sh claude-validator || fail "Claude plugin validator"

step "isolated Claude marketplace installation"
scripts/ci-provider-checks.sh claude-install || fail "isolated Claude installation"

step "isolated Codex marketplace installation"
scripts/ci-provider-checks.sh codex-install || fail "isolated Codex installation"

printf '\n'
if [[ "$failures" -gt 0 ]]; then
  echo "release check FAILED ($failures step(s))" >&2
  exit 1
fi
echo "release check passed"
