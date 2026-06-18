#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


sys.dont_write_bytecode = True

ORIGINS = {"claude", "codex"}
MODES = {
    "review",
    "research",
    "design",
    "plan",
    "debug",
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
    "max_local_subagents": 8,
    "web_research": "live",
}
REQUEST_KEYS = REQUIRED_REQUEST_KEYS | set(REQUEST_DEFAULTS)
RUN_ID_SAFE_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
MIN_AGENT_TIMEOUT_SECONDS = 2700
DEFAULT_AGENT_TIMEOUT_SECONDS = 2700
WEB_RESEARCH_CHOICES = {"cached", "live", "disabled"}
WEB_TOOLS = ("WebSearch", "WebFetch")
EDIT_TOOLS = ("Edit", "Write", "MultiEdit")
CLAUDE_DOCUMENTED_FLAGS = {
    "--model",
    "--effort",
    "--json-schema",
    "--output-format",
    "--no-session-persistence",
    "--permission-mode",
    "--dangerously-skip-permissions",
    "--tools",
    "--allowedTools",
    "--allowed-tools",
    "--disallowedTools",
    "--disallowed-tools",
    "--max-turns",
}
ROLE_BY_MODE = {
    "debug": "You are an independent root-cause investigator.",
    "design": "You are an independent software architect.",
    "plan": "You are an independent implementation planner.",
    "research": "You are an independent source-backed technical researcher.",
    "review": "You are an independent challenge-first software reviewer.",
}
MODE_CONTRACT_BY_MODE = {
    "debug": "Challenge the initial diagnosis. Prove or disprove likely root causes with repro evidence, logs, code paths, and the smallest decisive checks available.",
    "design": "Challenge whether the proposed approach is the right architecture. Compare viable alternatives, constraints, reversibility, operational risk, and repo fit before recommending a direction.",
    "plan": "Challenge whether the execution sequence is actually ready. Look for missing prerequisites, ordering hazards, rollback gaps, test gaps, and decisions that should be made before work starts.",
    "research": "Challenge whether the claimed facts are true, current, and applicable. Prefer latest official documentation and source-backed evidence; separate facts from inference and stale assumptions.",
    "review": "Challenge whether the work should ship. Prioritize correctness, security, regressions, missing tests, compatibility, and concrete file or command evidence.",
}
FRESHNESS_RULE = (
    "Freshness rule: When a material claim depends on current or external information, including APIs, "
    "product behavior, platform docs, dependency behavior, pricing, security advisories, laws, policies, "
    "or research, use the latest official documentation or primary sources. Do not rely on model memory "
    "for unstable facts. If online research is disabled or sources are unavailable, state that limitation "
    "explicitly and mark the claim as unverified."
)


class PeerReportValidationError(ValueError):
    pass


class RequestValidationError(ValueError):
    pass


@dataclass(frozen=True)
class PeerCommand:
    args: list[str]
    stdin: str | None


@dataclass(frozen=True)
class NormalizedPeerOutput:
    report: dict[str, Any]
    metadata: dict[str, Any]


def runtime_dir() -> Path:
    return Path(__file__).resolve().parent


def resource_root() -> Path:
    return runtime_dir().parent


def load_snapshot_runtime() -> Any:
    path = runtime_dir() / "snapshot.py"
    spec = importlib.util.spec_from_file_location("agent_collab_snapshot", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load snapshot runtime: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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
    unknown = set(request) - REQUEST_KEYS
    if unknown:
        raise RequestValidationError(f"request has unknown keys: {sorted(unknown)}")
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
    if request["web_research"] not in WEB_RESEARCH_CHOICES:
        raise RequestValidationError(f"web_research must be one of {sorted(WEB_RESEARCH_CHOICES)}")
    for key in ("target", "brief", "run_id"):
        if not isinstance(request[key], str) or not request[key].strip():
            raise RequestValidationError(f"{key} must be a non-empty string")
    if request["run_id"] in {".", ".."} or any(char not in RUN_ID_SAFE_CHARS for char in request["run_id"]):
        raise RequestValidationError("run_id must be a basename using only letters, numbers, '.', '_', or '-'")


def load_request(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RequestValidationError(f"invalid request JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RequestValidationError("request JSON must be an object")
    validate_request(data)
    return data


def fenced_block(info_string: str, text: str) -> str:
    max_run = 0
    current_run = 0
    for char in text:
        if char == "`":
            current_run += 1
            max_run = max(max_run, current_run)
        else:
            current_run = 0
    fence = "`" * max(3, max_run + 1)
    return "\n".join([f"{fence}{info_string}", text, fence])


def build_prompt(request: dict[str, Any], repo_root: Path, schema_path: Path) -> str:
    request = {**REQUEST_DEFAULTS, **request}
    contract = (resource_root() / "references" / "peer-only.md").read_text(encoding="utf-8").strip()
    prompt_blocks = (resource_root() / "references" / "peer-prompt-blocks.md").read_text(encoding="utf-8").strip()
    schema = schema_path.read_text(encoding="utf-8").strip()
    request_json = json.dumps(request, ensure_ascii=False, indent=2, sort_keys=True)
    edit_line = (
        "Edits are explicitly allowed by the request."
        if request["edit_allowed"]
        else "Do not modify files or produce any working-tree changes."
    )
    web_research = request.get("web_research", "live")
    if web_research == "disabled":
        web_line = "- Do not use online research. If current external facts are necessary, state that limitation explicitly and mark the claim as unverified."
    else:
        web_line = "- Use latest official documentation for external/API/platform/dependency/tooling claims. Research online when current external facts could affect the answer; prefer official sources and source-backed evidence."
    if request["local_subagents_allowed"] and request["max_local_subagents"] > 0:
        subagent_lines = [
            "- Use native local subagents when that improves independent coverage or speed.",
            "- If using local subagents, divide work by independent lenses, wait for their results, and merge only evidence-backed findings into this report.",
        ]
    else:
        subagent_lines = ["- Do not use local subagents for this peer run."]
    return "\n".join(
        [
            "# Agent Collab Peer Request",
            "",
            "<role>",
            ROLE_BY_MODE[request["mode"]],
            "You are the independent peer in Agent Collab. The host agent is the final synthesizer.",
            "</role>",
            "",
            "<objective>",
            f"Independently evaluate the target for `{request['mode']}` and return evidence-grounded findings.",
            "</objective>",
            "",
            "<challenge_contract>",
            "This is a challenge-first second opinion: assume the current answer may be wrong, seek disconfirming evidence, and do not accept host, peer, or user framing until it survives evidence checks.",
            "Agreement is a signal to inspect, not proof.",
            "</challenge_contract>",
            "",
            "<mode_contract>",
            MODE_CONTRACT_BY_MODE[request["mode"]],
            "</mode_contract>",
            "",
            "<freshness_contract>",
            FRESHNESS_RULE,
            "</freshness_contract>",
            "",
            "<context>",
            "Repository root:",
            fenced_block("text", str(repo_root)),
            "Target:",
            fenced_block("text", request["target"]),
            "</context>",
            "",
            "<success_criteria>",
            "- Inspect enough repository evidence to answer the task reliably.",
            "- Cite concrete files, commands, or observations for material claims.",
            web_line,
            *subagent_lines,
            "- Distinguish confirmed issues from uncertainty or product judgment.",
            "- Stop when the core request can be answered with useful evidence.",
            "</success_criteria>",
            "",
            "<constraints>",
            edit_line,
            f"Profile: {request['profile']}",
            f"Web research: {web_research}",
            f"Local subagents allowed: {str(request['local_subagents_allowed']).lower()}",
            f"Maximum local subagents: {request['max_local_subagents']}",
            "</constraints>",
            "",
            "<task_brief>",
            fenced_block("text", request["brief"].strip()),
            "</task_brief>",
            "",
            "<peer_contract>",
            contract,
            "</peer_contract>",
            "",
            "<prompt_contract>",
            prompt_blocks,
            "</prompt_contract>",
            "",
            "<request_json>",
            fenced_block("json", request_json),
            "</request_json>",
            "",
            "<response_schema>",
            fenced_block("json", schema),
            "</response_schema>",
            "",
            "<output_instruction>",
            "Respond with exactly one JSON object matching the schema.",
            "</output_instruction>",
        ]
    )


def web_research_mode(env: dict[str, str]) -> str:
    value = env.get("AGENT_COLLAB_WEB_RESEARCH", "live").strip() or "live"
    if value not in WEB_RESEARCH_CHOICES:
        raise ValueError(f"AGENT_COLLAB_WEB_RESEARCH must be one of {sorted(WEB_RESEARCH_CHOICES)}")
    return value


def codex_config_values(env: dict[str, str]) -> list[str]:
    raw = env.get("CODEX_AGENT_COLLAB_CONFIG", "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            raise ValueError("CODEX_AGENT_COLLAB_CONFIG must be a JSON list")
        values = parsed
    else:
        values = [item.strip() for item in raw.splitlines() if item.strip()]
    result: list[str] = []
    for item in values:
        text = str(item).strip()
        if not text:
            continue
        key, separator, _ = text.partition("=")
        if separator != "=" or not key.strip():
            raise ValueError("CODEX_AGENT_COLLAB_CONFIG entries must use key=value syntax")
        result.append(text)
    return result


def split_claude_tools(value: str) -> list[str]:
    return [item.strip() for item in value.replace(",", " ").split() if item.strip()]


def add_missing_tools(tools: list[str], additions: tuple[str, ...]) -> list[str]:
    seen = {tool.lower() for tool in tools}
    result = list(tools)
    for tool in additions:
        if tool.lower() not in seen:
            result.append(tool)
            seen.add(tool.lower())
    return result


def remove_tools(tools: list[str], removals: tuple[str, ...]) -> list[str]:
    removal_set = {tool.lower() for tool in removals}
    return [tool for tool in tools if tool.lower() not in removal_set]


def claude_allowed_tools_for_web(claude_tools: str, web_research: str) -> str:
    tools = split_claude_tools(claude_tools)
    if web_research == "disabled":
        tools = remove_tools(tools, WEB_TOOLS)
    else:
        tools = add_missing_tools(tools, WEB_TOOLS)
    return ",".join(tools)


def claude_disallowed_tools(safe_mode: bool, web_research: str) -> str:
    tools: list[str] = []
    if safe_mode:
        tools.extend(EDIT_TOOLS)
    if web_research == "disabled":
        tools.extend(WEB_TOOLS)
    return ",".join(tools)


def build_peer_command(
    request: dict[str, Any],
    prompt: str,
    repo_root: Path,
    schema_path: Path,
    output_path: Path,
    env: dict[str, str],
) -> PeerCommand:
    safe_mode = env_bool(env, "AGENT_COLLAB_SAFE_MODE")
    web_research = request.get("web_research") if request.get("web_research") in WEB_RESEARCH_CHOICES else web_research_mode(env)
    if request["peer"] == "claude":
        schema_inline = schema_path.read_text(encoding="utf-8")
        args = [
            "claude",
            "-p",
            "--model",
            env.get("CLAUDE_AGENT_COLLAB_MODEL", "opus"),
            "--effort",
            env.get("CLAUDE_AGENT_COLLAB_EFFORT", "max"),
            "--no-session-persistence",
            "--json-schema",
            schema_inline,
            "--output-format",
            "json",
        ]
        add_claude_permission_flags(args, env, safe_mode)
        add_claude_tool_flags(args, env, safe_mode, web_research)
        add_claude_optional_flags(args, env)
        return PeerCommand(args=args, stdin=prompt)

    sandbox = "read-only" if safe_mode else "danger-full-access"
    effort = env.get("CODEX_AGENT_COLLAB_EFFORT", "xhigh")
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
    ]
    for config in codex_config_values(env):
        args.extend(["-c", config])
    args.extend(
        [
            "-c",
            f'model_reasoning_effort="{effort}"',
            "-c",
            f'web_search="{web_research}"',
        ]
    )
    apply_codex_approval_flags(args, env, safe_mode)
    args.append("-")
    return PeerCommand(args=args, stdin=prompt)


def claude_supports_option(option: str, env: dict[str, str]) -> bool:
    override = env.get("AGENT_COLLAB_CLAUDE_ASSUME_FLAGS", "").strip().lower()
    if override in {"1", "true", "yes"}:
        return True
    if option in CLAUDE_DOCUMENTED_FLAGS:
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


def add_claude_permission_flags(args: list[str], env: dict[str, str], safe_mode: bool) -> None:
    if claude_supports_option("--permission-mode", env):
        args.extend(["--permission-mode", "plan" if safe_mode else "bypassPermissions"])
        return
    if not safe_mode and claude_supports_option("--dangerously-skip-permissions", env):
        args.append("--dangerously-skip-permissions")


def add_claude_tool_flags(args: list[str], env: dict[str, str], safe_mode: bool, web_research: str) -> None:
    claude_tools = env.get("CLAUDE_AGENT_COLLAB_TOOLS", "default").strip()
    disallowed_tools = claude_disallowed_tools(safe_mode, web_research)
    if disallowed_tools and claude_supports_option("--disallowedTools", env):
        args.extend(["--disallowedTools", disallowed_tools])
    if not claude_tools or claude_tools == "default":
        if claude_supports_option("--tools", env):
            args.extend(["--tools", "default"])
        return
    claude_tools = claude_allowed_tools_for_web(claude_tools, web_research)
    if claude_supports_option("--tools", env):
        args.extend(["--tools", claude_tools])
        return
    raise ValueError("custom Claude tool access requires Claude Code --tools support")


def add_claude_optional_flags(args: list[str], env: dict[str, str]) -> None:
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


def claude_api_error_details(stdout: str, stderr: str) -> tuple[str, dict[str, Any]] | None:
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(envelope, dict) or envelope.get("is_error") is not True:
        return None
    result = envelope.get("result")
    message = str(result).strip() if result is not None and str(result).strip() else "Claude peer returned an API error"
    detail_keys = [
        "api_error_status",
        "session_id",
        "duration_ms",
        "duration_api_ms",
        "num_turns",
        "stop_reason",
        "total_cost_usd",
        "permission_denials",
        "terminal_reason",
        "fast_mode_state",
    ]
    details = {key: envelope.get(key) for key in detail_keys if key in envelope}
    model_usage = envelope.get("modelUsage")
    if isinstance(model_usage, dict):
        details["modelUsage"] = model_usage
    usage = envelope.get("usage")
    if isinstance(usage, dict):
        details["usage"] = usage
    details["stdout_tail"] = stdout[-4000:]
    details["stderr_tail"] = stderr[-4000:]
    return message, details


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
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError("AGENT_COLLAB_TIMEOUT_SECONDS must be 0 or a positive number of seconds") from exc
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


def ignored_snapshot_paths(
    env: dict[str, str],
    raw_output_path: Path | None = None,
    normalization_output_path: Path | None = None,
) -> list[Path]:
    paths: list[Path] = []
    run_dir = env.get("AGENT_COLLAB_RUN_DIR", "").strip()
    if run_dir:
        paths.append(Path(run_dir))
    extra = env.get("AGENT_COLLAB_IGNORED_PATHS", "").strip()
    if extra:
        paths.extend(Path(item) for item in extra.split(os.pathsep) if item)
    if raw_output_path is not None:
        paths.append(raw_output_path)
    if normalization_output_path is not None:
        paths.append(normalization_output_path)
    return paths


def git_mutation_snapshot(repo_root: Path, ignored_paths: list[Path] | None = None) -> dict[str, Any]:
    snapshot = load_snapshot_runtime()
    return snapshot.mutation_snapshot(repo_root, ignored_paths=ignored_paths)


def mutation_error_details(before: dict[str, Any], after: dict[str, Any], peer_report: dict[str, Any]) -> dict[str, Any]:
    try:
        details = load_snapshot_runtime().diff_snapshots(before, after)
    except Exception:
        details = {
            "changed": before != after,
            "snapshot_mode": str(after.get("mode") or before.get("mode") or "unknown"),
            "before_digest": before.get("digest", ""),
            "after_digest": after.get("digest", ""),
            "changed_path_count": 0,
            "changed_paths": [],
            "changed_paths_truncated": False,
        }
    details["peer_report"] = peer_report
    return details


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


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def env_bool(env: dict[str, str], key: str, default: bool = False) -> bool:
    value = env.get(key)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"{key} must be one of 1/0, true/false, yes/no, or on/off")


def default_normalization_path(raw_output_path: Path | None) -> Path | None:
    if raw_output_path is None:
        return None
    return raw_output_path.with_name("peer-normalization.json")


def workspace_mutation_output_path(
    env: dict[str, str],
    raw_output_path: Path | None = None,
    normalization_output_path: Path | None = None,
) -> Path | None:
    run_dir = env.get("AGENT_COLLAB_RUN_DIR", "").strip()
    if run_dir:
        return Path(run_dir) / "workspace-mutation.json"
    if raw_output_path is not None:
        return raw_output_path.with_name("workspace-mutation.json")
    if normalization_output_path is not None:
        return normalization_output_path.with_name("workspace-mutation.json")
    return None


def _base_normalization_metadata(source: str, text: str, warnings: list[str] | None = None) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "source": source,
        "input_bytes": len(text.encode("utf-8")),
        "warnings": warnings or [],
        "validation_status": "not_checked",
    }


def _looks_like_claude_envelope(value: dict[str, Any]) -> bool:
    return value.get("type") == "result" or "subtype" in value or "structured_output" in value


def _validated_embedded_report(text: str, source: str) -> NormalizedPeerOutput:
    decoder = json.JSONDecoder()
    last_error: Exception | None = None
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if not isinstance(candidate, dict):
            continue
        try:
            validate_peer_report(candidate)
        except PeerReportValidationError as exc:
            last_error = exc
            continue
        return NormalizedPeerOutput(
            report=candidate,
            metadata=_base_normalization_metadata(
                source,
                text,
                ["Recovered a schema-valid peer report from surrounding text."],
            ),
        )
    if last_error is not None:
        raise ValueError(f"no schema-valid peer report found in text: {last_error}") from last_error
    raise ValueError("no JSON object found in text")


def normalize_json_payload(text: str) -> NormalizedPeerOutput:
    """Return a normalized report candidate; callers must validate the report schema."""
    if not text.strip():
        raise ValueError("peer output was empty")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return _validated_embedded_report(text, "embedded_json")

    if not isinstance(parsed, dict):
        raise ValueError("peer output must be a JSON object")

    structured_output = parsed.get("structured_output")
    if isinstance(structured_output, dict):
        return NormalizedPeerOutput(
            report=structured_output,
            metadata=_base_normalization_metadata("structured_output", text),
        )
    if "structured_output" in parsed and structured_output is not None:
        raise ValueError("structured_output must be a JSON object")

    result = parsed.get("result")
    if isinstance(result, dict):
        return NormalizedPeerOutput(
            report=result,
            metadata=_base_normalization_metadata("result_object", text),
        )
    if isinstance(result, str):
        try:
            nested = json.loads(result)
        except json.JSONDecodeError:
            return _validated_embedded_report(result, "result_embedded_json")
        if isinstance(nested, dict):
            return NormalizedPeerOutput(
                report=nested,
                metadata=_base_normalization_metadata("result_json", text),
            )
        raise ValueError("result was not a JSON object")

    if _looks_like_claude_envelope(parsed):
        subtype = parsed.get("subtype")
        errors = parsed.get("errors")
        detail = f"Claude result envelope did not include structured_output or a JSON result; subtype={subtype!r}"
        if errors:
            detail = f"{detail}; errors={errors!r}"
        raise ValueError(detail)

    return NormalizedPeerOutput(
        report=parsed,
        metadata=_base_normalization_metadata("direct_json", text),
    )


def parse_json_payload(text: str) -> dict[str, Any]:
    return normalize_json_payload(text).report


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
    if report["status"] == "ok" and "error" in report:
        raise PeerReportValidationError("ok reports must not include error")
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
    normalization_output_path: Path | None = None,
) -> dict[str, Any]:
    effective_env = dict(os.environ)
    if env is not None:
        effective_env.update(env)

    try:
        validate_request(request)
    except RequestValidationError as exc:
        return failure("invalid_request", str(exc), request)
    effective_env["AGENT_COLLAB_WEB_RESEARCH"] = request["web_research"]

    schema_path = default_schema_path()
    snapshot_ignored_paths = ignored_snapshot_paths(effective_env, raw_output_path, normalization_output_path)
    before_snapshot = git_mutation_snapshot(repo_root, snapshot_ignored_paths)
    mutation_output_path = workspace_mutation_output_path(effective_env, raw_output_path, normalization_output_path)

    def with_mutation_check(report: dict[str, Any]) -> dict[str, Any]:
        after_snapshot = git_mutation_snapshot(repo_root, snapshot_ignored_paths)
        if not request.get("edit_allowed", False) and after_snapshot != before_snapshot:
            mutation = mutation_error_details(before_snapshot, after_snapshot, report)
            mutation.pop("peer_report", None)
            mutation.update(
                {
                    "edit_allowed": False,
                    "message": "Workspace changed while edit_allowed=false; attribution is unknown.",
                }
            )
            if mutation_output_path is not None:
                write_json(mutation_output_path, mutation)
            if report.get("status") == "ok":
                limitations = list(report.get("limitations", []))
                warning = "Workspace changed while edit_allowed=false"
                if warning not in limitations:
                    limitations.append(warning)
                return {**report, "limitations": limitations}
            if isinstance(report.get("error"), dict):
                error = dict(report["error"])
                details = error.get("details")
                if isinstance(details, dict):
                    error["details"] = {**details, "workspace_mutation": mutation}
                else:
                    error["details"] = {
                        "original_details": details,
                        "workspace_mutation": mutation,
                    }
                return {**report, "error": error}
        return report

    with tempfile.TemporaryDirectory(prefix="agent-collab-") as tmp_name:
        tmp = Path(tmp_name)
        output_path = tmp / "peer-output.json"
        prompt = build_prompt(request, repo_root, schema_path)
        try:
            command = build_peer_command(request, prompt, repo_root, schema_path, output_path, effective_env)
            peer_timeout = timeout_seconds(effective_env)
        except ValueError as exc:
            return with_mutation_check(failure("invalid_configuration", str(exc), request))
        executable = command.args[0]
        if shutil.which(executable, path=effective_env.get("PATH")) is None:
            return with_mutation_check(failure("missing_cli", f"Required peer CLI not found on PATH: {executable}", request))

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
                timeout=peer_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return with_mutation_check(
                failure("timeout", "Peer run exceeded AGENT_COLLAB_TIMEOUT_SECONDS", request, str(exc))
            )
        except FileNotFoundError as exc:
            return with_mutation_check(failure("missing_cli", f"Required peer CLI not found: {executable}", request, str(exc)))

        if completed.returncode != 0:
            if raw_output_path is not None:
                raw_output_path.write_text(completed.stdout, encoding="utf-8")
            claude_api_error = claude_api_error_details(completed.stdout.strip(), completed.stderr)
            if claude_api_error is not None:
                message, details = claude_api_error
                return with_mutation_check(failure("peer_api_error", message, request, details))
            return with_mutation_check(
                failure(
                    "peer_nonzero_exit",
                    f"Peer CLI exited with status {completed.returncode}",
                    request,
                    {
                        "stdout_tail": completed.stdout[-4000:],
                        "stderr_tail": completed.stderr[-4000:],
                    },
                )
            )

        output_text = output_path.read_text(encoding="utf-8") if output_path.exists() else completed.stdout
        if raw_output_path is not None:
            raw_output_path.write_text(output_text, encoding="utf-8")
        normalization_output_path = normalization_output_path or default_normalization_path(raw_output_path)
        try:
            normalized = normalize_json_payload(output_text.strip())
            report = normalized.report
        except (json.JSONDecodeError, ValueError) as exc:
            if normalization_output_path is not None:
                write_json(
                    normalization_output_path,
                    {
                        "schema_version": "1.0",
                        "source": "none",
                        "input_bytes": len(output_text.encode("utf-8")),
                        "warnings": [],
                        "validation_status": "invalid_json",
                        "error": str(exc),
                    },
                )
            return with_mutation_check(failure("invalid_json", f"Peer output was not valid JSON: {exc}", request))

        try:
            validate_peer_report(report)
        except PeerReportValidationError as exc:
            if normalization_output_path is not None:
                metadata = dict(normalized.metadata)
                metadata["validation_status"] = "schema_validation_failed"
                metadata["error"] = str(exc)
                write_json(normalization_output_path, metadata)
            return with_mutation_check(failure("schema_validation_failed", str(exc), request, report))

        if normalization_output_path is not None:
            metadata = dict(normalized.metadata)
            metadata["validation_status"] = "ok"
            write_json(normalization_output_path, metadata)

        return with_mutation_check(report)


def run_request(
    request: dict[str, Any],
    repo_root: Path,
    env: dict[str, str] | None = None,
    raw_output_path: Path | None = None,
    normalization_output_path: Path | None = None,
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
    return invoke_peer(request, repo_root, effective_env, raw_output_path, normalization_output_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Invoke the Agent Collab cross-product peer once.")
    parser.add_argument("request_json", type=Path, help="Path to the Agent Collab request JSON file.")
    parser.add_argument("--repo-root", type=Path, default=None, help="Repository root. Defaults to git top-level.")
    parser.add_argument("--raw-output", type=Path, default=None, help="Optional path for the raw peer CLI JSON output.")
    parser.add_argument(
        "--normalization-output",
        type=Path,
        default=None,
        help="Optional path for peer output normalization metadata. Defaults next to --raw-output.",
    )
    args = parser.parse_args(argv)

    repo_root = (args.repo_root or default_repo_root()).resolve()
    try:
        request = load_request(args.request_json)
    except RequestValidationError as exc:
        result = failure("invalid_request", str(exc))
    else:
        result = run_request(
            request,
            repo_root,
            raw_output_path=args.raw_output,
            normalization_output_path=args.normalization_output,
        )

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
