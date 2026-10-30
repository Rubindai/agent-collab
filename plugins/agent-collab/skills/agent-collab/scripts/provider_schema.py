"""Build the self-contained provider projection of the canonical peer schema.

Only the reviewed omissions below are allowed. New constraints require an
explicit projection decision; they are never silently stripped for a provider.
"""
from __future__ import annotations

import argparse
import copy
from pathlib import Path
import sys

sys.dont_write_bytecode = True
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import strict_json as JSON


def provider_schema(canonical: dict) -> dict:
    value = copy.deepcopy(canonical)
    relations = [
        {"if": {"properties": {"origin": {"const": host}}, "required": ["origin"]},
         "then": {"properties": {"host": {"const": host}, "peer": {"const": peer}}}}
        for host, peer in (("claude", "codex"), ("codex", "claude"))
    ]
    error = value["properties"]["error"]["anyOf"][0]
    relations.extend([
        {"if": {"properties": {"status": {"const": "ok"}}, "required": ["status"]},
         "then": {"properties": {"error": {"const": None}}}},
        {"if": {"properties": {"status": {"const": "peer_failed"}}, "required": ["status"]},
         "then": {"properties": {"error": error}}},
    ])
    omissions = [
        ((), "$schema", "https://json-schema.org/draft/2020-12/schema"),
        ((), "$id", "https://local.agent-collab/peer-report.schema.json"),
        ((), "allOf", relations),
        (("properties", "run_id"), "minLength", 1),
        (("properties", "run_id"), "not", {"enum": [".", ".."]}),
        (("properties", "target"), "minLength", 1),
    ]
    for path, key, expected in omissions:
        node = value
        for part in path:
            node = node[part]
        if node.get(key) != expected:
            raise ValueError(f"unreviewed provider-schema omission at {'/'.join((*path, key))}")
        del node[key]

    def check(node):
        allowed = {"title", "description", "type", "properties", "required", "additionalProperties",
                   "items", "enum", "const", "anyOf", "pattern", "minItems"}
        if "minItems" in node and node["minItems"] not in (0, 1):
            raise ValueError("provider minItems must be 0 or 1")
        unknown = set(node) - allowed
        if unknown:
            raise ValueError(f"unreviewed provider-schema keywords: {sorted(unknown)}")
        for child in node.get("properties", {}).values():
            check(child)
        if "items" in node:
            check(node["items"])
        for child in node.get("anyOf", []):
            check(child)
    check(value)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    directory = Path(__file__).resolve().parent.parent / "schemas"
    output = directory / "peer-report-provider.schema.json"
    try:
        value = provider_schema(JSON.load(directory / "peer-report.schema.json"))
        text = JSON.dumps(value) + "\n"
        if args.check:
            if output.read_text(encoding="utf-8") != text:
                parser.exit(1, "provider schema differs from its canonical projection; run scripts/sync-packages.sh\n")
        else:
            JSON.write_text(output, text)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"provider schema generation failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
