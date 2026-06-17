#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ORIGINS = {"claude", "codex"}
MODES = {
    "review",
    "audit",
    "research",
    "design",
    "plan",
    "plan-critique",
    "debug",
    "migration",
    "test-strategy",
    "verify",
    "implement",
}
VERDICTS = {
    "pass",
    "pass_with_concerns",
    "changes_recommended",
    "ready",
    "needs_revision",
    "blocked",
    "informational",
}
CLAIM_STATUSES = {
    "confirmed",
    "plausible_unverified",
    "rejected",
    "product_decision",
    "needs_human_input",
}
TOP_LEVEL_KEYS = {
    "schema_version",
    "run_id",
    "origin",
    "host",
    "peer",
    "mode",
    "target",
    "status",
    "verdict",
    "summary",
    "findings",
    "claims",
    "limitations",
    "next_actions",
    "error",
}
REQUIRED_REPORT_KEYS = TOP_LEVEL_KEYS - {"error"}
REQUIRED_REQUEST_KEYS = {
    "origin",
    "host",
    "peer",
    "mode",
    "target",
    "brief",
    "edit_allowed",
    "run_id",
}
REQUEST_DEFAULTS = {
    "profile": "ultra",
    "local_subagents_allowed": True,
    "max_local_subagents": 6,
}
MIN_AGENT_TIMEOUT_SECONDS = 2700
DEFAULT_AGENT_TIMEOUT_SECONDS = 2700


class PeerReportValidationError(ValueError):
    pass


class RequestValidationError(ValueError):
    pass


@dataclass(frozen=True)
class PeerCommand:
    args: list[str]
    stdin: str | None


def runtime_dir() -> Path:
    return Path(__file__).resolve().parent


def resource_root() -> Path:
    return runtime_dir().parent


def default_schema_path() -> Path:
    return resource_root() / "schemas" / "peer-report.schema.json"


def default_repo_root() -> Path:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        return Path(completed.stdout.strip()).resolve()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return Path.cwd().resolve()


def validate_request(request: dict[str, Any]) -> None:
    for key, value in REQUEST_DEFAULTS.items():
        request.setdefault(key, value)
    missing = REQUIRED_REQUEST_KEYS - request.keys()
    if missing:
        raise RequestValidationError(f"request missing required keys: {sorted(missing)}")
    if request["origin"] not in ORIGINS:
        raise RequestValidationError("origin must be claude or codex")
    if request["host"] not in ORIGINS:
        raise RequestValidationError("host must be claude or codex")
    if request["peer"] not in ORIGINS:
        raise RequestValidationError("peer must be claude or codex")
    if request["host"] == request["peer"]:
        raise RequestValidationError("host and peer must be different products")
    if request["mode"] not in MODES:
        raise RequestValidationError(f"mode must be one of {sorted(MODES)}")
    if not isinstance(request["edit_allowed"], bool):
        raise RequestValidationError("edit_allowed must be a boolean")
    if request["profile"] not in {"standard", "max", "ultra"}:
        raise RequestValidationError("profile must be standard, max, or ultra")
    if not isinstance(request["local_subagents_allowed"], bool):
        raise RequestValidationError("local_subagents_allowed must be a boolean")
    if not isinstance(request["max_local_subagents"], int) or request["max_local_subagents"] < 0:
        raise RequestValidationError("max_local_subagents must be a non-negative integer")
    for key in ("target", "brief", "run_id"):
        if not isinstance(request[key], str) or not request[key].strip():
            raise RequestValidationError(f"{key} must be a non-empty string")


def load_request(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RequestValidationError(f"invalid request JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RequestValidationError("request JSON must be an object")
    validate_request(data)
    return data


def build_prompt(request: dict[str, Any], repo_root: Path, schema_path: Path) -> str:
    contract = (resource_root() / "references" / "peer-only.md").read_text(encoding="utf-8").strip()
    schema = schema_path.read_text(encoding="utf-8").strip()
    request_json = json.dumps(request, ensure_ascii=False, indent=2, sort_keys=True)
    edit_line = (
        "Edits are explicitly allowed by the request."
        if request["edit_allowed"]
        else "Do not modify files or produce any working-tree changes."
    )
    return "\n".join(
        [
            "Agent Collab peer request",
            "",
            "Objective:",
            f"Independently evaluate the target for `{request['mode']}` and return evidence-grounded findings.",
            "",
            "Context:",
            f"- Repository root: {repo_root}",
            f"- Target: {request['target']}",
            "",
            "Success criteria:",
            "- Inspect enough repository evidence to answer the task reliably.",
            "- Cite concrete files, commands, or observations for material claims.",
            "- Use latest official documentation for external/API/platform/dependency/tooling claims.",
            "- Research online extensively when current external facts could affect the answer. Prefer official sources and cite source-backed evidence.",
            "- Use native local subagents when that improves independent coverage or speed.",
            "- Distinguish confirmed issues from uncertainty or product judgment.",
            "- Stop when the core request can be answered with useful evidence.",
            "",
            "Constraints:",
            edit_line,
            f"Profile: {request['profile']}",
            f"Local subagents allowed: {str(request['local_subagents_allowed']).lower()}",
            f"Maximum local subagents: {request['max_local_subagents']}",
            "",
            "Task brief:",
            request["brief"].strip(),
            "",
            "Peer-only contract:",
            contract,
            "",
            "REQUEST JSON:",
            request_json,
            "",
            "RESPONSE SCHEMA:",
            schema,
            "",
            "Respond with exactly one JSON object matching the schema.",
        ]
    )


def build_peer_command(
    request: dict[str, Any],
    prompt: str,
    repo_root: Path,
    schema_path: Path,
    output_path: Path,
    env: dict[str, str],
) -> PeerCommand:
    safe_mode = env.get("AGENT_COLLAB_SAFE_MODE") == "1"
    if request["peer"] == "claude":
        schema_inline = schema_path.read_text(encoding="utf-8")
        permission_mode = "plan" if safe_mode else "bypassPermissions"
        claude_tools = env.get("CLAUDE_AGENT_COLLAB_TOOLS", "default")
        args = [
            "claude",
            "-p",
            "--model",
            env.get("CLAUDE_AGENT_COLLAB_MODEL", "opus"),
            "--effort",
            env.get("CLAUDE_AGENT_COLLAB_EFFORT", "max"),
            "--permission-mode",
            permission_mode,
            "--tools",
            claude_tools,
            "--no-session-persistence",
            "--json-schema",
            schema_inline,
            "--output-format",
            "json",
        ]
        add_claude_optional_flags(args, env)
        return PeerCommand(args=args, stdin=prompt)

    sandbox = "read-only" if safe_mode else "danger-full-access"
    effort = env.get("CODEX_AGENT_COLLAB_EFFORT", "xhigh")
    web_search = env.get("CODEX_AGENT_COLLAB_WEB_SEARCH", "live")
    args = [
        "codex",
        "exec",
        "--ephemeral",
        "--cd",
        str(repo_root),
        "--sandbox",
        sandbox,
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_path),
        "--model",
        env.get("CODEX_AGENT_COLLAB_MODEL", "gpt-5.5"),
        "-c",
        f'model_reasoning_effort="{effort}"',
        "-c",
        f'web_search="{web_search}"',
    ]
    apply_codex_approval_flags(args, env, safe_mode)
    args.append("-")
    return PeerCommand(args=args, stdin=prompt)


def claude_supports_option(option: str, env: dict[str, str]) -> bool:
    override = env.get("AGENT_COLLAB_CLAUDE_ASSUME_FLAGS", "").strip().lower()
    if override in {"1", "true", "yes"}:
        return True
    try:
        completed = subprocess.run(
            ["claude", "--help"],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return option in completed.stdout


def add_claude_optional_flags(args: list[str], env: dict[str, str]) -> None:
    if claude_supports_option("--max-budget-usd", env):
        args.extend(["--max-budget-usd", env.get("CLAUDE_AGENT_COLLAB_MAX_BUDGET_USD", "25.00")])
    if claude_supports_option("--max-turns", env):
        args.extend(["--max-turns", env.get("CLAUDE_AGENT_COLLAB_MAX_TURNS", "50")])


def codex_supports_ask_for_approval(env: dict[str, str]) -> bool:
    override = env.get("AGENT_COLLAB_CODEX_APPROVAL_FLAG", "").strip().lower()
    if override in {"ask", "ask-for-approval"}:
        return True
    if override in {"bypass", "dangerously-bypass"}:
        return False
    try:
        completed = subprocess.run(
            ["codex", "exec", "--help"],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return "--ask-for-approval" in completed.stdout


def apply_codex_approval_flags(args: list[str], env: dict[str, str], safe_mode: bool) -> None:
    if safe_mode:
        return
    if codex_supports_ask_for_approval(env):
        args.extend(["--ask-for-approval", "never"])
    else:
        args.append("--dangerously-bypass-approvals-and-sandbox")


def failure(
    kind: str,
    message: str,
    request: dict[str, Any] | None = None,
    details: Any | None = None,
) -> dict[str, Any]:
    request = request or {}
    return {
        "schema_version": "1.0",
        "run_id": str(request.get("run_id", "agent-collab-unknown")),
        "origin": request.get("origin", "codex") if request.get("origin") in ORIGINS else "codex",
        "host": request.get("host", "codex") if request.get("host") in ORIGINS else "codex",
        "peer": request.get("peer", "claude") if request.get("peer") in ORIGINS else "claude",
        "mode": request.get("mode", "review") if request.get("mode") in MODES else "review",
        "target": str(request.get("target", "")),
        "status": "peer_failed",
        "verdict": "blocked",
        "summary": message,
        "findings": [],
        "claims": [],
        "limitations": [message],
        "next_actions": [],
        "error": {
            "kind": kind,
            "message": message,
            "details": details,
        },
    }


def nested_invocation_requested(env: dict[str, str]) -> bool:
    if env.get("AGENT_COLLAB_PEER_ONLY", "").lower() == "true":
        return True
    try:
        cross_depth = int(env.get("AGENT_COLLAB_CROSS_AGENT_DEPTH", "0"))
        max_depth = int(env.get("AGENT_COLLAB_MAX_CROSS_AGENT_DEPTH", "1"))
        return cross_depth >= max_depth
    except ValueError:
        return True


def timeout_seconds(env: dict[str, str]) -> float | None:
    raw = env.get("AGENT_COLLAB_TIMEOUT_SECONDS", str(DEFAULT_AGENT_TIMEOUT_SECONDS)).strip()
    if not raw or raw == "0":
        return None
    value = float(raw)
    if value <= 0:
        return None
    return max(value, MIN_AGENT_TIMEOUT_SECONDS)


def git_status(repo_root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError:
        return ""
    return completed.stdout


def make_host_cli_guard(tmp: Path, host: str) -> Path:
    bin_dir = tmp / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper = bin_dir / host
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "{host} is the Agent Collab host; peer runs may not call the host CLI." >&2\n'
        "exit 64\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return bin_dir


def parse_json_payload(text: str) -> dict[str, Any]:
    parsed = json.loads(text)
    if isinstance(parsed, dict):
        structured_output = parsed.get("structured_output")
        if isinstance(structured_output, dict):
            return structured_output
        if "structured_output" in parsed and structured_output is not None:
            raise ValueError("structured_output must be a JSON object")
        if isinstance(parsed.get("result"), str):
            try:
                nested = json.loads(parsed["result"])
            except json.JSONDecodeError as exc:
                raise ValueError("result was not a JSON object") from exc
            if isinstance(nested, dict):
                return nested
            raise ValueError("result was not a JSON object")
        return parsed
    raise ValueError("peer output must be a JSON object")


def validate_peer_report(report: dict[str, Any]) -> None:
    unknown = set(report) - TOP_LEVEL_KEYS
    if unknown:
        raise PeerReportValidationError(f"unknown top-level keys: {sorted(unknown)}")
    missing = REQUIRED_REPORT_KEYS - report.keys()
    if missing:
        raise PeerReportValidationError(f"missing required keys: {sorted(missing)}")
    if report["schema_version"] != "1.0":
        raise PeerReportValidationError("schema_version must be 1.0")
    if report["origin"] not in ORIGINS or report["host"] not in ORIGINS or report["peer"] not in ORIGINS:
        raise PeerReportValidationError("origin, host, and peer must be claude or codex")
    if report["mode"] not in MODES:
        raise PeerReportValidationError("invalid mode")
    if report["status"] not in {"ok", "peer_failed"}:
        raise PeerReportValidationError("invalid status")
    if report["verdict"] not in VERDICTS:
        raise PeerReportValidationError("invalid verdict")
    for key in ("run_id", "target", "summary"):
        if not isinstance(report[key], str):
            raise PeerReportValidationError(f"{key} must be a string")
    for key in ("findings", "claims", "limitations", "next_actions"):
        if not isinstance(report[key], list):
            raise PeerReportValidationError(f"{key} must be an array")
    for finding in report["findings"]:
        if not isinstance(finding, dict):
            raise PeerReportValidationError("finding must be an object")
        unknown = set(finding) - {"severity", "title", "details", "files", "recommendation", "confidence"}
        if unknown:
            raise PeerReportValidationError(f"finding has unknown keys: {sorted(unknown)}")
        for key in ("severity", "title", "details", "confidence"):
            if key not in finding:
                raise PeerReportValidationError(f"finding missing {key}")
        if finding["severity"] not in {"critical", "high", "medium", "low", "info"}:
            raise PeerReportValidationError("invalid finding severity")
        if finding["confidence"] not in {"high", "medium", "low"}:
            raise PeerReportValidationError("invalid finding confidence")
        for key in ("title", "details"):
            if not isinstance(finding[key], str):
                raise PeerReportValidationError(f"finding {key} must be a string")
        if "recommendation" in finding and not isinstance(finding["recommendation"], str):
            raise PeerReportValidationError("finding recommendation must be a string")
        if "files" in finding:
            if not isinstance(finding["files"], list) or not all(isinstance(item, str) for item in finding["files"]):
                raise PeerReportValidationError("finding files must be an array of strings")
    for claim in report["claims"]:
        if not isinstance(claim, dict):
            raise PeerReportValidationError("claim must be an object")
        unknown = set(claim) - {"claim", "status", "evidence"}
        if unknown:
            raise PeerReportValidationError(f"claim has unknown keys: {sorted(unknown)}")
        for key in ("claim", "status", "evidence"):
            if key not in claim:
                raise PeerReportValidationError(f"claim missing {key}")
        if not isinstance(claim["claim"], str):
            raise PeerReportValidationError("claim claim must be a string")
        if claim.get("status") not in CLAIM_STATUSES:
            raise PeerReportValidationError("invalid claim status")
        if not isinstance(claim["evidence"], str):
            raise PeerReportValidationError("claim evidence must be a string")
    for key in ("limitations", "next_actions"):
        if not all(isinstance(item, str) for item in report[key]):
            raise PeerReportValidationError(f"{key} must contain strings")
    if report["status"] == "peer_failed" and "error" not in report:
        raise PeerReportValidationError("peer_failed reports must include error")
    if "error" in report:
        error = report["error"]
        if not isinstance(error, dict):
            raise PeerReportValidationError("error must be an object")
        unknown = set(error) - {"kind", "message", "details"}
        if unknown:
            raise PeerReportValidationError(f"error has unknown keys: {sorted(unknown)}")
        for key in ("kind", "message"):
            if not isinstance(error.get(key), str):
                raise PeerReportValidationError(f"error {key} must be a string")


def invoke_peer(
    request: dict[str, Any],
    repo_root: Path,
    env: dict[str, str] | None = None,
    raw_output_path: Path | None = None,
) -> dict[str, Any]:
    effective_env = dict(os.environ)
    if env is not None:
        effective_env.update(env)

    try:
        validate_request(request)
    except RequestValidationError as exc:
        return failure("invalid_request", str(exc), request)

    schema_path = default_schema_path()
    before_status = git_status(repo_root)

    with tempfile.TemporaryDirectory(prefix="agent-collab-") as tmp_name:
        tmp = Path(tmp_name)
        output_path = tmp / "peer-output.json"
        prompt = build_prompt(request, repo_root, schema_path)
        command = build_peer_command(request, prompt, repo_root, schema_path, output_path, effective_env)
        executable = command.args[0]
        if shutil.which(executable, path=effective_env.get("PATH")) is None:
            return failure("missing_cli", f"Required peer CLI not found on PATH: {executable}", request)

        peer_env = dict(effective_env)
        peer_env["AGENT_COLLAB_PEER_ONLY"] = "true"
        peer_env["AGENT_COLLAB_CROSS_AGENT_DEPTH"] = "1"
        peer_env["AGENT_COLLAB_MAX_CROSS_AGENT_DEPTH"] = "1"
        peer_env["AGENT_COLLAB_LOCAL_SUBAGENT_DEPTH"] = "0"
        peer_env["AGENT_COLLAB_MAX_LOCAL_SUBAGENT_DEPTH"] = "1"
        peer_env["AGENT_COLLAB_HOST"] = request["host"]
        peer_env["AGENT_COLLAB_PEER"] = request["peer"]
        peer_env["AGENT_COLLAB_RUN_ID"] = request["run_id"]
        guard_bin = make_host_cli_guard(tmp, request["host"])
        peer_env["PATH"] = f"{guard_bin}{os.pathsep}{peer_env.get('PATH', '')}"

        try:
            completed = subprocess.run(
                command.args,
                cwd=repo_root,
                env=peer_env,
                input=command.stdin,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds(effective_env),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return failure("timeout", "Peer run exceeded AGENT_COLLAB_TIMEOUT_SECONDS", request, str(exc))
        except FileNotFoundError as exc:
            return failure("missing_cli", f"Required peer CLI not found: {executable}", request, str(exc))

        if completed.returncode != 0:
            if raw_output_path is not None:
                raw_output_path.write_text(completed.stdout, encoding="utf-8")
            return failure(
                "peer_nonzero_exit",
                f"Peer CLI exited with status {completed.returncode}",
                request,
                {
                    "stdout_tail": completed.stdout[-4000:],
                    "stderr_tail": completed.stderr[-4000:],
                },
            )

        output_text = output_path.read_text(encoding="utf-8") if output_path.exists() else completed.stdout
        if raw_output_path is not None:
            raw_output_path.write_text(output_text, encoding="utf-8")
        try:
            report = parse_json_payload(output_text.strip())
        except (json.JSONDecodeError, ValueError) as exc:
            return failure("invalid_json", f"Peer output was not valid JSON: {exc}", request)

        try:
            validate_peer_report(report)
        except PeerReportValidationError as exc:
            return failure("schema_validation_failed", str(exc), request, report)

        after_status = git_status(repo_root)
        if not request.get("edit_allowed", False) and after_status != before_status:
            return failure(
                "unexpected_working_tree_mutation",
                "Peer changed the working tree while edit_allowed=false",
                request,
                {
                    "before": before_status,
                    "after": after_status,
                    "peer_report": report,
                },
            )

        return report


def run_request(
    request: dict[str, Any],
    repo_root: Path,
    env: dict[str, str] | None = None,
    raw_output_path: Path | None = None,
) -> dict[str, Any]:
    effective_env = dict(os.environ)
    if env is not None:
        effective_env.update(env)
    try:
        validate_request(request)
    except RequestValidationError as exc:
        return failure("invalid_request", str(exc), request)
    if nested_invocation_requested(effective_env):
        return failure("nested_invocation_refused", "Agent Collab peer invocation is already in progress", request)
    return invoke_peer(request, repo_root, effective_env, raw_output_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Invoke the Agent Collab cross-product peer once.")
    parser.add_argument("request_json", type=Path, help="Path to the Agent Collab request JSON file.")
    parser.add_argument("--repo-root", type=Path, default=None, help="Repository root. Defaults to git top-level.")
    parser.add_argument("--raw-output", type=Path, default=None, help="Optional path for the raw peer CLI JSON output.")
    args = parser.parse_args(argv)

    repo_root = (args.repo_root or default_repo_root()).resolve()
    try:
        request = load_request(args.request_json)
    except RequestValidationError as exc:
        result = failure("invalid_request", str(exc))
    else:
        result = run_request(request, repo_root, raw_output_path=args.raw_output)

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
