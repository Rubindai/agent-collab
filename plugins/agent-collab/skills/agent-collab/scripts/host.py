#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


if os.name == "posix":
    import fcntl
elif os.name == "nt":
    import msvcrt


sys.dont_write_bytecode = True

ORIGINS = {"claude", "codex"}
MODES = {
    "review",
    "research",
    "design",
    "plan",
    "debug",
}
ARTIFACT_SCHEMA_VERSION = "2.0"
SETTINGS_SCHEMA_VERSION = "2.0"
DEFAULT_HISTORY_RETAINED_RUNS = 50
SETTING_DEFAULTS: dict[str, Any] = {
    "profile": "ultra",
    "local_subagents_allowed": True,
    "max_local_subagents": 8,
    "agent_timeout_seconds": "2700",
    "safe_mode": False,
    "codex_model": "gpt-5.6-sol",
    "codex_effort": "max",
    "online_research": True,
    "codex_config": [],
    "claude_model": "claude-opus-4-8",
    "claude_effort": "max",
    "claude_tools": "default",
    "claude_max_turns": "50",
    "history_retained_runs": DEFAULT_HISTORY_RETAINED_RUNS,
}
SETTING_ENV_KEYS = {
    "profile": "AGENT_COLLAB_PROFILE",
    "local_subagents_allowed": "AGENT_COLLAB_LOCAL_SUBAGENTS_ALLOWED",
    "max_local_subagents": "AGENT_COLLAB_MAX_LOCAL_SUBAGENTS",
    "agent_timeout_seconds": "AGENT_COLLAB_TIMEOUT_SECONDS",
    "safe_mode": "AGENT_COLLAB_SAFE_MODE",
    "codex_model": "CODEX_AGENT_COLLAB_MODEL",
    "codex_effort": "CODEX_AGENT_COLLAB_EFFORT",
    "online_research": "AGENT_COLLAB_ONLINE_RESEARCH",
    "codex_config": "CODEX_AGENT_COLLAB_CONFIG",
    "claude_model": "CLAUDE_AGENT_COLLAB_MODEL",
    "claude_effort": "CLAUDE_AGENT_COLLAB_EFFORT",
    "claude_tools": "CLAUDE_AGENT_COLLAB_TOOLS",
    "claude_max_turns": "CLAUDE_AGENT_COLLAB_MAX_TURNS",
    "history_retained_runs": "AGENT_COLLAB_HISTORY_RETAINED_RUNS",
}
PROFILE_CHOICES = {"standard", "max", "ultra"}
MIN_CLI_VERSIONS = {"codex": (0, 144, 5), "claude": (2, 1, 214)}
DEFAULT_AVAILABILITY_TIMEOUT_SECONDS = 30.0
MAX_AVAILABILITY_TIMEOUT_SECONDS = 60.0
CLAIM_STATUSES = {
    "confirmed",
    "plausible_unverified",
    "rejected",
    "product_decision",
    "needs_human_input",
}
CLAIM_SHAPE_CONTRACT = (
    "expected claim/status/evidence shape: object with string 'claim', 'status' one of "
    f"{sorted(CLAIM_STATUSES)}, and string 'evidence'; join multiple evidence items with '; '; "
    "do not use id/type as substitutes or array-valued evidence"
)
CLAIM_SOURCES = {"host", "peer", "helper", "adjudicator"}
ADJUDICATOR_STATUSES = {"advisory_pending", "ok", "needs_human_input", "blocked"}
RECOMMENDED_VERDICTS = {
    "pass",
    "pass_with_concerns",
    "changes_recommended",
    "ready",
    "needs_revision",
    "blocked",
    "informational",
}
RUN_ID_SAFE_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
MIN_PEER_WAIT_SECONDS = 2700
FINISH_TIMEOUT_GRACE_SECONDS = 30
FINISH_WAIT_POLL_SECONDS = 1
PEER_REPORT_STABLE_SECONDS = 0.5
RESET_SCOPES = {"local", "global", "all"}
RUN_ARTIFACT_NAMES = {
    "host-request.json",
    "peer-process.json",
    "provider-process.json",
    "peer-report.json",
    "host-result.json",
    "host-synthesis.json",
    "workspace-mutation.json",
}
WORKSPACE_GUARD_SCHEMA_VERSION = "2.0"
WORKSPACE_GUARD_GIT_PATH = "agent-collab/active-v2.json"
WORKSPACE_GUARD_LOCK_TIMEOUT_SECONDS = 10.0
WORKSPACE_GUARD_LOCK_POLL_SECONDS = 0.02
WORKSPACE_GUARD_KEYS = {
    "schema_version",
    "run_id",
    "repo_root",
    "run_dir",
    "host",
    "phase",
    "created_at",
    "launcher_identity",
    "peer_identity",
}
PROVIDER_PROCESS_KEYS = {
    "schema_version",
    "run_id",
    "pid",
    "pgid",
    "process_identity",
    "status",
    "cleanup_outcome",
    "completed_at",
}


def runtime_dir() -> Path:
    return Path(__file__).resolve().parent


def resource_root() -> Path:
    return runtime_dir().parent


def repo_storage_id(repo_root: Path) -> str:
    digest = hashlib.sha256(str(repo_root).encode("utf-8")).hexdigest()[:12]
    safe_name = "".join(char if char.isalnum() or char in "._-" else "-" for char in repo_root.name).strip(".-")
    return f"{safe_name[:48] or 'repo'}-{digest}"


def default_state_root() -> Path:
    if os.environ.get("AGENT_COLLAB_STATE_HOME"):
        return Path(os.environ["AGENT_COLLAB_STATE_HOME"]).expanduser()
    if os.environ.get("CLAUDE_PLUGIN_DATA"):
        return Path(os.environ["CLAUDE_PLUGIN_DATA"]).expanduser()

    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    try:
        if resource_root().resolve().is_relative_to(codex_home.resolve()):
            return codex_home / "agent-collab"
    except OSError:
        pass

    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")).expanduser()
    return base / "agent-collab"


def default_storage_root(repo_root: Path) -> Path:
    root = resource_root().resolve()
    source_root = (repo_root / "tools" / "agent-collab").resolve()
    if root == source_root:
        return source_root
    return default_state_root() / "repos" / repo_storage_id(repo_root)


def default_run_root(repo_root: Path) -> Path:
    return default_storage_root(repo_root) / "runs"


def default_local_settings_path(repo_root: Path) -> Path:
    return default_storage_root(repo_root) / "settings.local.json"


def default_global_settings_path() -> Path:
    if os.environ.get("AGENT_COLLAB_HOME"):
        return Path(os.environ["AGENT_COLLAB_HOME"]).expanduser() / "settings.json"
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")).expanduser()
    return base / "agent-collab" / "settings.json"


def settings_path(scope: str, repo_root: Path) -> Path:
    if scope == "local":
        return default_local_settings_path(repo_root)
    if scope == "global":
        return default_global_settings_path()
    raise ValueError(f"invalid settings scope: {scope}")


def default_repo_root() -> Path:
    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode == 0 and completed.stdout.strip():
        return Path(completed.stdout.strip()).resolve()
    return Path.cwd().resolve()


def parse_cli_version(text: str) -> tuple[int, int, int] | None:
    match = re.search(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)", text)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def require_peer_cli_version(peer: str) -> str:
    minimum = MIN_CLI_VERSIONS[peer]
    executable = shutil.which(peer)
    if executable is None:
        raise SystemExit(f"required peer CLI not found on PATH: {peer}")
    try:
        completed = subprocess.run(
            [peer, "--version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f"could not determine {peer} CLI version: {exc}") from exc
    raw_version = f"{completed.stdout}\n{completed.stderr}".strip()
    parsed = parse_cli_version(raw_version)
    minimum_text = ".".join(str(part) for part in minimum)
    if completed.returncode != 0 or parsed is None:
        raise SystemExit(f"could not determine {peer} CLI version; Agent Collab requires {minimum_text} or newer")
    if parsed < minimum:
        found = ".".join(str(part) for part in parsed)
        raise SystemExit(f"{peer} CLI {found} is unsupported; install {minimum_text} or newer")
    return ".".join(str(part) for part in parsed)


def configured_timeout_seconds(value: Any) -> float:
    return float(normalize_timeout(value))


def load_peer_runtime() -> Any:
    path = runtime_dir() / "peer.py"
    spec = importlib.util.spec_from_file_location("agent_collab_peer", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load peer runtime: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_availability_runtime() -> Any:
    path = runtime_dir() / "availability.py"
    spec = importlib.util.spec_from_file_location("agent_collab_availability", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load availability runtime: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_safety_runtime() -> Any:
    path = runtime_dir() / "safety.py"
    spec = importlib.util.spec_from_file_location("agent_collab_safety_host", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load safety runtime: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_strict_json_runtime() -> Any:
    path = runtime_dir() / "strict_json.py"
    spec = importlib.util.spec_from_file_location("agent_collab_strict_json_host", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load strict JSON runtime: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SAFETY = load_safety_runtime()
STRICT_JSON = load_strict_json_runtime()


def load_snapshot_runtime() -> Any:
    path = runtime_dir() / "snapshot.py"
    spec = importlib.util.spec_from_file_location("agent_collab_snapshot", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load snapshot runtime: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_state_runtime() -> Any:
    path = runtime_dir() / "state.py"
    spec = importlib.util.spec_from_file_location("agent_collab_state", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load state runtime: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read_text_arg(value: str | None, file_value: Path | None) -> str:
    if file_value is not None:
        return STRICT_JSON.read_text(file_value, max_bytes=4 * 1024 * 1024).strip()
    return (value or "").strip()


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def classify_mode(target: str, brief: str) -> str:
    text = f"{target}\n{brief}".casefold()
    review_context = _contains_any(
        text,
        (
            "review",
            "diff",
            "patch",
            "pull request",
            "pr ",
            "safe to ship",
            "ship",
            "missing test",
            "regression",
            "security",
            "auth",
        ),
    )
    debug_context = _contains_any(
        text,
        (
            "bug",
            "crash",
            "stack trace",
            "traceback",
            "exception",
            "root cause",
            "reproduce",
            "repro",
            "failing",
            "failure",
            " hung",
            "hanging",
            "timeout",
            "broken",
            "error",
        ),
    )
    plan_context = _contains_any(
        text,
        (
            "plan",
            "test strategy",
            "rollout",
            "checklist",
            "sequence",
            "steps",
            "milestone",
            "implementation order",
            "execution order",
            "rollback",
        ),
    )
    design_context = _contains_any(
        text,
        (
            "architecture",
            "architect",
            "design",
            "tradeoff",
            "trade-off",
            "alternative",
            "approach",
            "migration approach",
            "moving storage",
            "storage provider",
            "api shape",
            "schema design",
        ),
    )
    research_context = _contains_any(
        text,
        (
            "research",
            "official doc",
            "official api",
            "source-backed",
            "source backed",
            "external evidence",
            "current docs",
            "changed recently",
            "platform behavior",
            "dependency behavior",
        ),
    )

    if debug_context and not plan_context:
        return "debug"
    if plan_context:
        return "plan"
    if design_context:
        return "design"
    if research_context and not review_context:
        return "research"
    return "review"


def utc_run_id(host: str, mode: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = os.urandom(3).hex()
    return f"{stamp}-{host}-{mode}-{suffix}"


def is_safe_run_id(run_id: str) -> bool:
    return bool(run_id) and run_id not in {".", ".."} and all(char in RUN_ID_SAFE_CHARS for char in run_id)


def require_safe_run_id(run_id: str) -> str:
    if not is_safe_run_id(run_id):
        raise ValueError("run_id must be a non-empty basename using only letters, numbers, '.', '_', or '-'")
    return run_id


def write_json(path: Path, data: dict[str, Any]) -> None:
    STRICT_JSON.write(path, data)


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    raise ValueError(f"expected boolean value, got {value!r}")


def normalize_timeout(value: Any) -> str:
    raw = str(value).strip()
    try:
        seconds = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout must be a finite number of seconds") from exc
    if not MIN_PEER_WAIT_SECONDS <= seconds <= SAFETY.MAX_AGENT_TIMEOUT_SECONDS:
        raise ValueError(
            f"timeout must be between {MIN_PEER_WAIT_SECONDS} and {SAFETY.MAX_AGENT_TIMEOUT_SECONDS} seconds"
        )
    return str(int(seconds)) if seconds.is_integer() else str(seconds)


def normalize_codex_config(value: Any, source: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = STRICT_JSON.loads(text, max_bytes=1_000_000)
            except ValueError as exc:
                raise ValueError(f"{source} codex_config must be a JSON list or key=value string") from exc
            value = parsed
        else:
            value = [item.strip() for item in text.splitlines() if item.strip()]
    if not isinstance(value, list):
        raise ValueError(f"{source} codex_config must be a list of key=value strings")

    normalized: list[str] = []
    for item in value:
        text = str(item).strip()
        if not text:
            continue
        key, separator, _ = text.partition("=")
        if separator != "=" or not key.strip():
            raise ValueError(f"{source} codex_config entries must use key=value syntax")
        normalized.append(text)
    return normalized


def is_fable_model(value: Any) -> bool:
    normalized = str(value).strip().casefold()
    return normalized == "fable" or normalized.startswith("claude-fable-")


def normalize_settings(raw_settings: dict[str, Any], source: str) -> dict[str, Any]:
    unknown = set(raw_settings) - set(SETTING_DEFAULTS)
    if unknown:
        raise ValueError(f"{source} contains unknown setting keys: {sorted(unknown)}")
    normalized: dict[str, Any] = {}
    for key, value in raw_settings.items():
        if key in {"local_subagents_allowed", "safe_mode", "online_research"}:
            normalized[key] = parse_bool(value)
        elif key == "max_local_subagents":
            number = int(value)
            if not 0 <= number <= SAFETY.MAX_LOCAL_SUBAGENTS:
                raise ValueError(
                    f"{source} max_local_subagents must be between 0 and {SAFETY.MAX_LOCAL_SUBAGENTS}"
                )
            normalized[key] = number
        elif key == "history_retained_runs":
            number = int(value)
            if number < 0:
                raise ValueError(f"{source} history_retained_runs must be non-negative")
            normalized[key] = number
        elif key == "profile":
            text = str(value).strip()
            if text not in PROFILE_CHOICES:
                raise ValueError(f"{source} profile must be one of {sorted(PROFILE_CHOICES)}")
            normalized[key] = text
        elif key in {"codex_effort", "claude_effort"}:
            text = str(value).strip()
            if not text:
                raise ValueError(f"{source} {key} must be a non-empty exact effort identifier")
            normalized[key] = text
        elif key == "codex_config":
            normalized[key] = normalize_codex_config(value, source)
        elif key == "agent_timeout_seconds":
            normalized[key] = normalize_timeout(value)
        elif key == "claude_max_turns":
            text = str(value).strip()
            if not text or not 1 <= int(text) <= SAFETY.MAX_CLAUDE_TURNS:
                raise ValueError(
                    f"{source} claude_max_turns must be between 1 and {SAFETY.MAX_CLAUDE_TURNS}"
                )
            normalized[key] = text
        else:
            text = str(value).strip()
            if not text:
                raise ValueError(f"{source} {key} must not be empty")
            if key == "claude_model" and is_fable_model(text):
                raise ValueError(
                    f"{source} claude_model cannot persist Fable; request Fable 5 explicitly "
                    "with the per-run --peer-model claude-fable-5 override"
                )
            normalized[key] = text
    return normalized


def validate_v2_settings_types(raw_settings: dict[str, Any], source: str) -> dict[str, Any]:
    """Validate a persisted v2 settings object without legacy coercion."""
    bool_keys = {"local_subagents_allowed", "safe_mode", "online_research"}
    int_keys = {"max_local_subagents", "history_retained_runs"}
    string_keys = {"profile", "agent_timeout_seconds", "codex_model", "codex_effort", "claude_model", "claude_effort", "claude_tools", "claude_max_turns"}
    for key in bool_keys:
        if type(raw_settings[key]) is not bool:
            raise ValueError(f"{source} {key} must be a JSON boolean")
    for key in int_keys:
        if type(raw_settings[key]) is not int or raw_settings[key] < 0:
            raise ValueError(f"{source} {key} must be a non-negative JSON integer")
    if raw_settings["max_local_subagents"] > SAFETY.MAX_LOCAL_SUBAGENTS:
        raise ValueError(
            f"{source} max_local_subagents must not exceed {SAFETY.MAX_LOCAL_SUBAGENTS}"
        )
    for key in string_keys:
        if not isinstance(raw_settings[key], str) or not raw_settings[key].strip():
            raise ValueError(f"{source} {key} must be a non-empty JSON string")
    if raw_settings["profile"] not in PROFILE_CHOICES:
        raise ValueError(f"{source} profile must be one of {sorted(PROFILE_CHOICES)}")
    if is_fable_model(raw_settings["claude_model"]):
        raise ValueError(
            f"{source} claude_model cannot persist Fable; use a per-run --peer-model override"
        )
    if re.fullmatch(r"[1-9][0-9]*(?:\.[0-9]+)?", raw_settings["agent_timeout_seconds"]) is None:
        raise ValueError(f"{source} agent_timeout_seconds must be a canonical positive numeric string")
    normalize_timeout(raw_settings["agent_timeout_seconds"])
    if re.fullmatch(r"[1-9][0-9]*", raw_settings["claude_max_turns"]) is None:
        raise ValueError(f"{source} claude_max_turns must be a canonical positive integer string")
    if int(raw_settings["claude_max_turns"]) > SAFETY.MAX_CLAUDE_TURNS:
        raise ValueError(f"{source} claude_max_turns must not exceed {SAFETY.MAX_CLAUDE_TURNS}")
    codex_config = raw_settings["codex_config"]
    if not isinstance(codex_config, list) or not all(
        isinstance(item, str) and re.match(r"^[^=]+=", item) for item in codex_config
    ):
        raise ValueError(f"{source} codex_config must be a JSON array of key=value strings")
    return {key: list(value) if isinstance(value, list) else value for key, value in raw_settings.items()}


def load_settings_file(path: Path, source: str) -> tuple[dict[str, Any], str | None]:
    if not path.exists():
        return {}, None
    try:
        data = STRICT_JSON.load(path, max_bytes=4 * 1024 * 1024)
        if not isinstance(data, dict):
            raise ValueError("settings file must contain a JSON object")
        expected_keys = {"schema_version", "updated_at", "settings"}
        if set(data) != expected_keys:
            raise ValueError(f"settings file must contain exactly {sorted(expected_keys)}")
        if data.get("schema_version") != SETTINGS_SCHEMA_VERSION:
            raise ValueError(
                f"settings schema_version must be {SETTINGS_SCHEMA_VERSION}; legacy settings are not supported"
            )
        if not isinstance(data.get("updated_at"), str) or not data["updated_at"]:
            raise ValueError("settings file updated_at must be a non-empty string")
        raw_settings = data.get("settings")
        if not isinstance(raw_settings, dict):
            raise ValueError("settings file must contain a settings object")
        if set(raw_settings) != set(SETTING_DEFAULTS):
            missing = sorted(set(SETTING_DEFAULTS) - set(raw_settings))
            unknown = sorted(set(raw_settings) - set(SETTING_DEFAULTS))
            raise ValueError(f"settings keys must match v2 exactly; missing={missing}, unknown={unknown}")
        return validate_v2_settings_types(raw_settings, source), None
    except Exception as exc:
        return {}, str(exc)


def load_settings_layers(repo_root: Path) -> dict[str, Any]:
    global_path = default_global_settings_path()
    local_path = default_local_settings_path(repo_root)
    global_settings, global_error = load_settings_file(global_path, "global settings")
    local_settings, local_error = load_settings_file(local_path, "local settings")
    return {
        "global": {"path": str(global_path), "settings": global_settings, "error": global_error},
        "local": {"path": str(local_path), "settings": local_settings, "error": local_error},
    }


def resolve_settings(repo_root: Path, env: dict[str, str] | None = None) -> dict[str, Any]:
    if env is None:
        env = os.environ
    layers = load_settings_layers(repo_root)
    effective = {
        key: list(value) if isinstance(value, list) else value
        for key, value in SETTING_DEFAULTS.items()
    }
    sources = {key: "builtin" for key in effective}
    environment_errors: list[str] = []
    for scope in ("global", "local"):
        for key, value in layers[scope]["settings"].items():
            effective[key] = value
            sources[key] = scope
    for key, env_key in SETTING_ENV_KEYS.items():
        if env_key in env:
            try:
                effective[key] = normalize_settings({key: env[env_key]}, f"environment {env_key}")[key]
            except (TypeError, ValueError) as exc:
                environment_errors.append(str(exc))
            else:
                sources[key] = f"env:{env_key}"
    return {
        "settings": effective,
        "sources": sources,
        "layers": layers,
        "environment_errors": environment_errors,
    }


def settings_error_messages(resolved: dict[str, Any]) -> list[str]:
    layer_errors = [
        f"{scope} settings ({layer['path']}): {layer['error']}"
        for scope, layer in resolved["layers"].items()
        if layer.get("error")
    ]
    return layer_errors + [
        f"environment settings: {error}"
        for error in resolved.get("environment_errors", [])
    ]


def require_valid_settings(resolved: dict[str, Any]) -> None:
    errors = settings_error_messages(resolved)
    if errors:
        detail = "\n".join(f"- {error}" for error in errors)
        raise SystemExit(f"invalid Agent Collab settings; refusing to use defaults:\n{detail}")


def settings_to_env(settings: dict[str, Any]) -> dict[str, str]:
    return {
        "AGENT_COLLAB_PROFILE": str(settings["profile"]),
        "AGENT_COLLAB_LOCAL_SUBAGENTS_ALLOWED": "1" if settings["local_subagents_allowed"] else "0",
        "AGENT_COLLAB_MAX_LOCAL_SUBAGENTS": str(settings["max_local_subagents"]),
        "AGENT_COLLAB_TIMEOUT_SECONDS": str(settings["agent_timeout_seconds"]),
        "AGENT_COLLAB_SAFE_MODE": "1" if settings["safe_mode"] else "0",
        "CODEX_AGENT_COLLAB_MODEL": str(settings["codex_model"]),
        "CODEX_AGENT_COLLAB_EFFORT": str(settings["codex_effort"]),
        "AGENT_COLLAB_ONLINE_RESEARCH": "1" if settings["online_research"] else "0",
        "CODEX_AGENT_COLLAB_CONFIG": json.dumps(settings["codex_config"], ensure_ascii=False),
        "CLAUDE_AGENT_COLLAB_MODEL": str(settings["claude_model"]),
        "CLAUDE_AGENT_COLLAB_EFFORT": str(settings["claude_effort"]),
        "CLAUDE_AGENT_COLLAB_TOOLS": str(settings["claude_tools"]),
        "CLAUDE_AGENT_COLLAB_MAX_TURNS": str(settings["claude_max_turns"]),
        "AGENT_COLLAB_HISTORY_RETAINED_RUNS": str(settings["history_retained_runs"]),
    }


def write_settings_file(path: Path, settings: dict[str, Any]) -> None:
    normalized = normalize_settings(settings, str(path))
    normalized = validate_v2_settings_types(normalized, str(path))
    write_json(
        path,
        {
            "schema_version": SETTINGS_SCHEMA_VERSION,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "settings": normalized,
        },
    )


def git_output(repo_root: Path, args: list[str]) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError:
        return ""
    return completed.stdout.strip()


def snapshot_json_path(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.name}.json")


def run_snapshot(
    repo_root: Path,
    output_path: Path,
    ignored_paths: list[Path] | None = None,
) -> dict[str, Any]:
    snapshot = load_snapshot_runtime()
    deadline = time.monotonic() + snapshot.DEFAULT_SNAPSHOT_TIMEOUT_SECONDS
    data = snapshot.mutation_snapshot(
        repo_root,
        ignored_paths=ignored_paths,
        deadline=deadline,
    )
    write_json(snapshot_json_path(output_path), data)
    snapshot.write_workspace_snapshot(
        repo_root,
        output_path,
        ignored_paths=ignored_paths,
        deadline=deadline,
        snapshot_data=data,
    )
    return data


def record_host_workspace_mutation(
    repo_root: Path,
    run_dir: Path,
    request: dict[str, Any],
) -> dict[str, Any] | None:
    before_path = snapshot_json_path(run_dir / "before.snapshot")
    if not before_path.exists() or before_path.stat().st_size == 0:
        raise ArtifactValidationError("canonical before.snapshot.json is missing or empty")
    before = load_json(before_path)
    after = run_snapshot(repo_root, run_dir / "after.snapshot", ignored_paths=[run_dir])
    snapshot = load_snapshot_runtime()
    diff = snapshot.diff_snapshots(before, after)
    mutation_path = run_dir / "workspace-mutation.json"
    if diff["changed"]:
        diagnostic = {
            **diff,
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "source": "host_snapshot",
            "edit_allowed": bool(request["edit_allowed"]),
            "message": (
                "Workspace changed during the Agent Collab run; attribution is unknown."
            ),
            "coverage_limitations": [
                "Known high-volume generated and dependency directories are excluded.",
                "Transient changes that were reverted before a snapshot are not observable.",
            ],
        }
        write_json(mutation_path, diagnostic)
        return diagnostic
    return read_json_if_exists(mutation_path)


def workspace_mutation_sha256(value: dict[str, Any] | None) -> str:
    """Bind synthesis to the exact mutation evidence visible at finish."""

    canonical = STRICT_JSON.dumps(value, indent=None, sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def json_number(value: float | None) -> float | int | None:
    if value is None:
        return None
    return int(value) if float(value).is_integer() else value


def utc_timestamp(epoch_seconds: float | None = None) -> str:
    if epoch_seconds is None:
        epoch_seconds = time.time()
    return datetime.fromtimestamp(epoch_seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc_timestamp(value: Any) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def peer_started_at_epoch(process_info: dict[str, Any], run_dir: Path) -> float | None:
    raw_epoch = process_info.get("started_at_epoch")
    if raw_epoch is not None:
        try:
            return float(raw_epoch)
        except (TypeError, ValueError):
            pass

    parsed = parse_utc_timestamp(process_info.get("started_at"))
    if parsed is not None:
        return parsed

    process_path = run_dir / "peer-process.json"
    try:
        return process_path.stat().st_mtime
    except FileNotFoundError:
        return None


def peer_elapsed_seconds(process_info: dict[str, Any], run_dir: Path, now: float | None = None) -> float | None:
    started_at = peer_started_at_epoch(process_info, run_dir)
    if started_at is None:
        return None
    if now is None:
        now = time.time()
    return max(0.0, now - started_at)


def path_is_creatable(path: Path) -> bool:
    current = path
    while not current.exists():
        parent = current.parent
        if parent == current:
            return False
        current = parent
    return os.access(current, os.W_OK)


def file_info(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.exists(),
        "bytes": path.stat().st_size if path.exists() else 0,
    }


def read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    return load_json(path)


def workspace_guard_path(repo_root: Path) -> Path:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--git-path", WORKSPACE_GUARD_GIT_PATH],
            cwd=repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError:
        completed = None
    if completed is not None and completed.returncode == 0 and completed.stdout.strip():
        candidate = Path(completed.stdout.strip())
        return (candidate if candidate.is_absolute() else repo_root / candidate).resolve()
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")).expanduser()
    return (base / "agent-collab" / "workspace-guards" / f"{repo_storage_id(repo_root)}.json").resolve()


def workspace_guard_lock_path(repo_root: Path) -> Path:
    guard_path = workspace_guard_path(repo_root)
    return guard_path.with_name(f"{guard_path.name}.lock")


@contextmanager
def workspace_guard_transaction(repo_root: Path):
    """Serialize every guard read-modify-write transition across processes."""

    lock_path = workspace_guard_lock_path(repo_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    acquired = False
    deadline = time.monotonic() + WORKSPACE_GUARD_LOCK_TIMEOUT_SECONDS
    try:
        if os.name == "nt" and os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        while True:
            try:
                if os.name == "posix":
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                elif os.name == "nt":
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    raise RuntimeError(
                        f"workspace guard locking is unsupported on platform {os.name!r}"
                    )
                acquired = True
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"timed out acquiring Agent Collab workspace guard lock: {lock_path}"
                    )
                time.sleep(WORKSPACE_GUARD_LOCK_POLL_SECONDS)
        yield
    finally:
        if acquired:
            try:
                if os.name == "posix":
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                elif os.name == "nt":
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        os.close(descriptor)


def _read_workspace_guard_unlocked(repo_root: Path) -> tuple[Path, dict[str, Any] | None]:
    path = workspace_guard_path(repo_root)
    if not path.exists():
        return path, None
    try:
        parsed = STRICT_JSON.load(path, max_bytes=1_000_000)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"invalid active Agent Collab workspace guard at {path}: {exc}") from exc
    if (
        not isinstance(parsed, dict)
        or set(parsed) != WORKSPACE_GUARD_KEYS
        or parsed.get("schema_version") != WORKSPACE_GUARD_SCHEMA_VERSION
    ):
        raise SystemExit(f"invalid active Agent Collab workspace guard at {path}; manual inspection is required")
    if not is_safe_run_id(str(parsed.get("run_id") or "")):
        raise SystemExit(f"invalid active Agent Collab run_id in workspace guard at {path}")
    if parsed["host"] not in ORIGINS or parsed["phase"] not in {"starting", "peer_running", "ready_for_synthesis"}:
        raise SystemExit(f"invalid active Agent Collab host/phase in workspace guard at {path}")
    if Path(str(parsed["repo_root"])).resolve() != repo_root.resolve():
        raise SystemExit(f"active Agent Collab workspace guard repo_root mismatch at {path}")
    if not isinstance(parsed["run_dir"], str) or not parsed["run_dir"]:
        raise SystemExit(f"invalid active Agent Collab run_dir in workspace guard at {path}")
    if parse_utc_timestamp(parsed["created_at"]) is None:
        raise SystemExit(f"invalid active Agent Collab created_at in workspace guard at {path}")
    try:
        SAFETY.validate_process_identity(parsed["launcher_identity"])
        if parsed["peer_identity"] is not None:
            SAFETY.validate_process_identity(parsed["peer_identity"])
    except ValueError as exc:
        raise SystemExit(f"invalid process identity in workspace guard at {path}: {exc}") from exc
    if parsed["phase"] == "starting" and parsed["peer_identity"] is not None:
        raise SystemExit(f"starting Agent Collab guard must not contain peer_identity at {path}")
    if parsed["phase"] != "starting" and parsed["peer_identity"] is None:
        raise SystemExit(f"active Agent Collab guard is missing peer_identity at {path}")
    return path, parsed


def read_workspace_guard(repo_root: Path) -> tuple[Path, dict[str, Any] | None]:
    with workspace_guard_transaction(repo_root):
        return _read_workspace_guard_unlocked(repo_root)


def _require_workspace_guard_available_unlocked(repo_root: Path) -> None:
    path, active = _read_workspace_guard_unlocked(repo_root)
    if active is None:
        return
    if active["phase"] == "starting" and not SAFETY.process_identity_matches(active["launcher_identity"]):
        # A launcher that died before it could create a peer cannot complete or
        # cancel its workflow. This narrow stale-start recovery prevents a
        # permanent worktree wedge without unlocking a running/synthesis run.
        path.unlink(missing_ok=True)
        return
    raise SystemExit(
        "nested or concurrent Agent Collab start refused by the workspace recursion guard; "
        f"active run={active['run_id']} guard={path}"
    )


def require_workspace_guard_available(repo_root: Path) -> None:
    with workspace_guard_transaction(repo_root):
        _require_workspace_guard_available_unlocked(repo_root)


def acquire_workspace_guard(repo_root: Path, run_id: str, host: str, run_dir: Path) -> Path:
    path = workspace_guard_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": WORKSPACE_GUARD_SCHEMA_VERSION,
        "run_id": run_id,
        "repo_root": str(repo_root),
        "run_dir": str(run_dir),
        "host": host,
        "phase": "starting",
        "created_at": utc_timestamp(),
        "launcher_identity": SAFETY.process_identity(os.getpid()),
        "peer_identity": None,
    }
    with workspace_guard_transaction(repo_root):
        _require_workspace_guard_available_unlocked(repo_root)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError as exc:
            raise SystemExit(f"could not acquire Agent Collab workspace guard: {path}") from exc
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as guard:
                guard.write(STRICT_JSON.dumps(payload) + "\n")
        except BaseException:
            path.unlink(missing_ok=True)
            raise
    return path


def update_workspace_guard(
    repo_root: Path,
    run_id: str,
    *,
    phase: str,
    peer_identity: dict[str, Any],
) -> Path:
    if phase not in {"peer_running", "ready_for_synthesis"}:
        raise ValueError("workspace guard update phase is invalid")
    with workspace_guard_transaction(repo_root):
        path, active = _read_workspace_guard_unlocked(repo_root)
        if active is None or active["run_id"] != run_id:
            raise SystemExit(f"cannot update missing or foreign Agent Collab workspace guard for {run_id}")
        SAFETY.validate_process_identity(peer_identity)
        STRICT_JSON.write(path, {**active, "phase": phase, "peer_identity": peer_identity})
    return path


def _require_workspace_guard_owner_unlocked(repo_root: Path, run_id: str) -> Path:
    path, active = _read_workspace_guard_unlocked(repo_root)
    if active is None:
        raise SystemExit(f"active Agent Collab workspace guard is missing for run {run_id}")
    if active["run_id"] != run_id:
        raise SystemExit(
            f"Agent Collab workspace guard belongs to run {active['run_id']}, not {run_id}; refusing to release it"
        )
    return path


def require_workspace_guard_owner(repo_root: Path, run_id: str) -> Path:
    with workspace_guard_transaction(repo_root):
        return _require_workspace_guard_owner_unlocked(repo_root, run_id)


def release_workspace_guard(repo_root: Path, run_id: str) -> Path:
    with workspace_guard_transaction(repo_root):
        path = _require_workspace_guard_owner_unlocked(repo_root, run_id)
        path.unlink()
    return path


def require_artifact_version(data: dict[str, Any], label: str) -> None:
    if data.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise SystemExit(
            f"{label} schema_version must be {ARTIFACT_SCHEMA_VERSION}; legacy runs are not supported"
        )


def validate_peer_process(data: dict[str, Any], request: dict[str, Any], run_dir: Path) -> None:
    required = {
        "schema_version",
        "pid",
        "pgid",
        "process_identity",
        "run_id",
        "run_dir",
        "repo_root",
        "host",
        "peer",
        "profile",
        "started_at",
        "started_at_epoch",
        "peer_timeout_seconds",
        "peer_cli_version",
        "workspace_guard",
        "settings",
        "peer_report",
        "peer_raw",
        "peer_normalization",
        "provider_process",
        "host_first_pass",
    }
    if set(data) != required:
        raise ArtifactValidationError(
            f"peer-process keys must match v2 exactly; missing={sorted(required - set(data))}, unknown={sorted(set(data) - required)}"
        )
    if data["schema_version"] != ARTIFACT_SCHEMA_VERSION:
        raise ArtifactValidationError("peer-process schema_version must be 2.0; legacy artifacts are not supported")
    for key in ("run_id", "host", "peer", "profile"):
        if data[key] != request[key]:
            raise ArtifactValidationError(f"peer-process {key} does not match host request")
    if Path(str(data["run_dir"])).resolve() != run_dir.resolve():
        raise ArtifactValidationError("peer-process run_dir does not match the resolved run directory")
    for key in ("pid", "pgid"):
        if type(data[key]) is not int or data[key] <= 0:
            raise ArtifactValidationError(f"peer-process {key} must be a positive integer")
    try:
        SAFETY.validate_process_identity(data["process_identity"])
    except ValueError as exc:
        raise ArtifactValidationError(f"peer-process process_identity is invalid: {exc}") from exc
    if data["process_identity"]["pid"] != data["pid"] or data["process_identity"]["pgid"] != data["pgid"]:
        raise ArtifactValidationError("peer-process identity pid/pgid must match peer-process pid/pgid")
    if not isinstance(data["started_at"], str) or parse_utc_timestamp(data["started_at"]) is None:
        raise ArtifactValidationError("peer-process started_at must be an ISO-8601 timestamp")
    if not isinstance(data["started_at_epoch"], (int, float)) or isinstance(data["started_at_epoch"], bool):
        raise ArtifactValidationError("peer-process started_at_epoch must be numeric")
    if not math.isfinite(float(data["started_at_epoch"])):
        raise ArtifactValidationError("peer-process started_at_epoch must be finite")
    timeout = data["peer_timeout_seconds"]
    if (
        not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or not MIN_PEER_WAIT_SECONDS <= float(timeout) <= SAFETY.MAX_AGENT_TIMEOUT_SECONDS
    ):
        raise ArtifactValidationError("peer-process peer_timeout_seconds must be a finite bounded number")
    for key in (
        "repo_root",
        "peer_cli_version",
        "workspace_guard",
        "peer_report",
        "peer_raw",
        "peer_normalization",
        "provider_process",
        "host_first_pass",
    ):
        if not isinstance(data[key], str) or not data[key]:
            raise ArtifactValidationError(f"peer-process {key} must be a non-empty string")
    settings = data["settings"]
    if not isinstance(settings, dict) or set(settings) != {"local", "global"}:
        raise ArtifactValidationError("peer-process settings must contain exactly local and global paths")
    if not all(isinstance(value, str) and value for value in settings.values()):
        raise ArtifactValidationError("peer-process settings paths must be non-empty strings")
    expected_paths = {
        "peer_report": run_dir / "peer-report.json",
        "peer_raw": run_dir / "peer.raw.json",
        "peer_normalization": run_dir / "peer-normalization.json",
        "provider_process": run_dir / "provider-process.json",
        "host_first_pass": run_dir / "host-first-pass.json",
    }
    for key, expected_path in expected_paths.items():
        if Path(data[key]).resolve() != expected_path.resolve():
            raise ArtifactValidationError(f"peer-process {key} path does not match the v2 run layout")


def validate_provider_process(data: dict[str, Any], request: dict[str, Any]) -> None:
    if not isinstance(data, dict) or set(data) != PROVIDER_PROCESS_KEYS:
        raise ArtifactValidationError("provider-process keys must match schema 2.0 exactly")
    if data["schema_version"] != ARTIFACT_SCHEMA_VERSION or data["run_id"] != request["run_id"]:
        raise ArtifactValidationError("provider-process schema_version/run_id does not match the request")
    status = data["status"]
    if status not in {"pending", "running", "quiescent", "cleanup_failed"}:
        raise ArtifactValidationError("provider-process status is invalid")
    if status == "pending":
        if any(data[key] is not None for key in ("pid", "pgid", "process_identity", "cleanup_outcome", "completed_at")):
            raise ArtifactValidationError("pending provider-process must not claim a launched process")
        return
    for key in ("pid", "pgid"):
        if type(data[key]) is not int or data[key] <= 0:
            raise ArtifactValidationError(f"provider-process {key} must be a positive integer")
    if data["process_identity"] is not None:
        try:
            SAFETY.validate_process_identity(data["process_identity"])
        except ValueError as exc:
            raise ArtifactValidationError(f"provider-process identity is invalid: {exc}") from exc
        if (
            data["process_identity"]["pid"] != data["pid"]
            or data["process_identity"]["pgid"] != data["pgid"]
        ):
            raise ArtifactValidationError("provider-process identity pid/pgid mismatch")
    elif not (
        status == "cleanup_failed"
        or (
            status == "quiescent"
            and data["cleanup_outcome"] == "identity_mismatch_group_empty"
        )
    ):
        raise ArtifactValidationError(
            "provider-process identity is required after launch unless an empty group proves terminal cleanup"
        )
    if status == "running":
        if data["cleanup_outcome"] is not None or data["completed_at"] is not None:
            raise ArtifactValidationError("running provider-process cannot claim cleanup completion")
    else:
        if not isinstance(data["cleanup_outcome"], str) or not data["cleanup_outcome"]:
            raise ArtifactValidationError("terminal provider-process requires cleanup_outcome")
        if parse_utc_timestamp(data["completed_at"]) is None:
            raise ArtifactValidationError("terminal provider-process requires completed_at")


def load_provider_process(run_dir: Path, request: dict[str, Any]) -> dict[str, Any]:
    path = run_dir / "provider-process.json"
    if not path.exists() or path.stat().st_size == 0:
        raise ArtifactValidationError("provider-process.json is missing or empty")
    data = load_json(path)
    validate_provider_process(data, request)
    return data


def require_process_tree_quiescent(
    process_info: dict[str, Any],
    run_dir: Path,
    request: dict[str, Any],
    *,
    wrapper_quiescence_proven: bool = False,
) -> dict[str, Any]:
    if not wrapper_quiescence_proven:
        if process_identity_matches(process_info) and process_alive(int(process_info["pid"])):
            raise ArtifactValidationError("peer wrapper is still running")
        if SAFETY.process_group_alive(int(process_info["pgid"])):
            raise ArtifactValidationError("peer wrapper group still has live members")
    provider = load_provider_process(run_dir, request)
    if provider["status"] == "pending":
        return provider
    if provider["status"] != "quiescent":
        raise ArtifactValidationError(
            f"provider process group is not terminal and quiescent: {provider['status']}"
        )
    return provider


def require_run_request(run_dir: Path) -> dict[str, Any]:
    request = load_json(run_dir / "host-request.json")
    require_artifact_version(request, "host-request")
    try:
        load_peer_runtime().validate_request(request)
    except Exception as exc:
        raise SystemExit(f"invalid v2 host request in {run_dir}: {exc}") from exc
    return request


def stderr_tail(path: Path, limit: int = 4000) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - max(limit * 4, limit)))
        payload = handle.read(max(limit * 4, limit))
    return payload.decode("utf-8", errors="replace")[-limit:]


def ensure_run_dir(path: Path, run_root: Path, *, allow_external: bool = False) -> Path:
    resolved = path.resolve()
    root = resolved.parent if allow_external else run_root
    if is_direct_run_dir(root, resolved):
        return resolved
    scope = "a valid Agent Collab run directory" if allow_external else f"a direct child of {run_root}"
    raise SystemExit(f"invalid Agent Collab run directory: {resolved} must be {scope}")


def resolve_run_reference(repo_root: Path, reference: str | None, run_root: Path | None = None) -> Path:
    root = (run_root or default_run_root(repo_root)).resolve()
    if reference:
        candidate = Path(reference).expanduser()
        if (candidate.is_absolute() or len(candidate.parts) > 1) and candidate.exists():
            return ensure_run_dir(candidate, root, allow_external=True)
        state = load_state_runtime()
        if is_safe_run_id(reference):
            job = state.find_job(root, reference)
            if job and job.get("run_dir"):
                return ensure_run_dir(Path(str(job["run_dir"])).expanduser(), root)
        candidate = root / reference
        if candidate.exists():
            return ensure_run_dir(candidate, root)
        raise SystemExit(f"unknown Agent Collab run: {reference}")

    state = load_state_runtime()
    job = state.find_job(root, None)
    if job and job.get("run_dir"):
        return ensure_run_dir(Path(str(job["run_dir"])).expanduser(), root)
    raise SystemExit(f"no Agent Collab runs found under {root}")


def job_patch_from_run(run_dir: Path, status: str, phase: str, **extra: Any) -> dict[str, Any]:
    request = read_json_if_exists(run_dir / "host-request.json") or {}
    process_info = read_json_if_exists(run_dir / "peer-process.json") or {}
    return {
        "id": str(request.get("run_id") or process_info.get("run_id") or run_dir.name),
        "run_dir": str(run_dir),
        "repo_root": str(process_info.get("repo_root") or ""),
        "host": request.get("host") or process_info.get("host"),
        "peer": request.get("peer") or process_info.get("peer"),
        "mode": request.get("mode"),
        "target": request.get("target"),
        "profile": request.get("profile") or process_info.get("profile"),
        "status": status,
        "phase": phase,
        **extra,
    }


def summarize_run(run_dir: Path) -> dict[str, Any]:
    request = require_run_request(run_dir)
    process_info = load_json(run_dir / "peer-process.json")
    try:
        validate_peer_process(process_info, request, run_dir)
    except ArtifactValidationError as exc:
        raise SystemExit(f"invalid peer-process artifact in {run_dir}: {exc}") from exc
    pid = process_info.get("pid")
    host_result = read_json_if_exists(run_dir / "host-result.json") or {}
    phase = host_result.get("phase")
    wrapper_alive = (
        process_identity_matches(process_info) and process_alive(int(pid))
        if isinstance(pid, int)
        else False
    )
    wrapper_group_active = False
    if phase not in {"ready_for_synthesis", "done"} and isinstance(process_info.get("pgid"), int):
        wrapper_group_active = SAFETY.process_group_alive(int(process_info["pgid"]))
    try:
        provider = load_provider_process(run_dir, request)
        provider_status = provider["status"]
    except (OSError, ValueError, ArtifactValidationError):
        provider = None
        provider_status = "invalid"
    provider_active = provider_status in {"running", "cleanup_failed", "invalid"}
    process_tree_quiescent = not wrapper_alive and not wrapper_group_active and provider_status in {
        "pending",
        "quiescent",
    }
    peer_alive = not process_tree_quiescent
    peer_report_incomplete = False
    try:
        peer_report = read_json_if_exists(run_dir / "peer-report.json")
    except (OSError, ValueError) as exc:
        if process_tree_quiescent:
            raise SystemExit(f"terminal peer-report.json is invalid: {exc}") from exc
        peer_report = None
        peer_report_incomplete = True
    normalization = read_json_if_exists(run_dir / "peer-normalization.json")
    workspace_mutation = read_json_if_exists(run_dir / "workspace-mutation.json")
    elapsed_seconds = peer_elapsed_seconds(process_info, run_dir)
    early_cancel_blocked = (
        peer_alive
        and elapsed_seconds is not None
        and elapsed_seconds < MIN_PEER_WAIT_SECONDS
    )
    return {
        "run_id": process_info.get("run_id", run_dir.name),
        "run_dir": str(run_dir),
        "peer_pid": pid,
        "peer_alive": peer_alive,
        "wrapper_alive": wrapper_alive,
        "wrapper_group_active": wrapper_group_active,
        "provider_status": provider_status,
        "provider_active": provider_active,
        "process_tree_quiescent": process_tree_quiescent,
        "elapsed_seconds": json_number(elapsed_seconds),
        "minimum_wait_seconds": MIN_PEER_WAIT_SECONDS,
        "minimum_wait_remaining_seconds": (
            json_number(max(0.0, MIN_PEER_WAIT_SECONDS - elapsed_seconds))
            if elapsed_seconds is not None
            else None
        ),
        "early_cancel_blocked": early_cancel_blocked,
        "empty_output_guidance": "Empty peer-report.json or stderr does not imply a stalled peer while the peer process is alive.",
        "phase": phase,
        "peer_status": peer_report.get("status") if peer_report else None,
        "peer_verdict": peer_report.get("verdict") if peer_report else None,
        "peer_report_incomplete": peer_report_incomplete,
        "normalization_source": normalization.get("source") if normalization else None,
        "validation_status": normalization.get("validation_status") if normalization else None,
        "workspace_mutation": workspace_mutation,
        "artifacts": {
            "host_request": file_info(run_dir / "host-request.json"),
            "before_snapshot": file_info(run_dir / "before.snapshot"),
            "peer_process": file_info(run_dir / "peer-process.json"),
            "host_first_pass": file_info(run_dir / "host-first-pass.json"),
            "peer_raw": file_info(run_dir / "peer.raw.json"),
            "peer_normalization": file_info(run_dir / "peer-normalization.json"),
            "peer_report": file_info(run_dir / "peer-report.json"),
            "claim_matrix": file_info(run_dir / "claim-matrix.json"),
            "adjudicator_report": file_info(run_dir / "adjudicator-report.json"),
            "after_snapshot": file_info(run_dir / "after.snapshot"),
            "workspace_mutation": file_info(run_dir / "workspace-mutation.json"),
            "peer_stderr": file_info(run_dir / "peer.stderr.log"),
        },
    }


def resolve_finish_wait_timeout(
    explicit_timeout_seconds: float | None,
    process_info: dict[str, Any],
) -> tuple[float, str]:
    if explicit_timeout_seconds is not None:
        if not MIN_PEER_WAIT_SECONDS <= explicit_timeout_seconds <= SAFETY.MAX_AGENT_TIMEOUT_SECONDS + FINISH_TIMEOUT_GRACE_SECONDS:
            raise ValueError(
                f"finish timeout must be between {MIN_PEER_WAIT_SECONDS} and "
                f"{SAFETY.MAX_AGENT_TIMEOUT_SECONDS + FINISH_TIMEOUT_GRACE_SECONDS} seconds"
            )
        return explicit_timeout_seconds, "explicit"

    if "peer_timeout_seconds" in process_info:
        peer_timeout = process_info["peer_timeout_seconds"]
        try:
            derived = float(peer_timeout) + FINISH_TIMEOUT_GRACE_SECONDS
            if not MIN_PEER_WAIT_SECONDS <= float(peer_timeout) <= SAFETY.MAX_AGENT_TIMEOUT_SECONDS:
                raise ValueError("peer timeout is outside the finite supported range")
            return derived, "peer_timeout_plus_grace"
        except (TypeError, ValueError) as exc:
            raise ValueError("peer-process peer_timeout_seconds must be finite and bounded") from exc

    raise ValueError("peer-process is missing required peer_timeout_seconds")


def remaining_finish_wait_seconds(
    process_info: dict[str, Any],
    configured_wait_seconds: float,
    *,
    now: float | None = None,
) -> tuple[float, float]:
    current_time = time.time() if now is None else now
    hard_deadline = (
        float(process_info["started_at_epoch"])
        + float(process_info["peer_timeout_seconds"])
        + FINISH_TIMEOUT_GRACE_SECONDS
    )
    return min(configured_wait_seconds, max(0.0, hard_deadline - current_time)), hard_deadline


def wait_for_peer_report(
    peer_report_path: Path,
    process_info: dict[str, Any],
    timeout_seconds: float,
) -> str:
    pid = int(process_info["pid"])
    start = time.time()
    deadline = start + timeout_seconds
    last_signature: tuple[int, int] | None = None
    stable_since: float | None = None
    while True:
        if peer_report_path.exists() and peer_report_path.stat().st_size > 0:
            stat = peer_report_path.stat()
            signature = (stat.st_size, stat.st_mtime_ns)
            alive = process_identity_matches(process_info) and process_alive(pid)
            if not alive:
                return "report_ready"
            now = time.time()
            if signature != last_signature:
                last_signature = signature
                stable_since = now
            if now >= deadline:
                return "timeout"
            # Report bytes are not terminal while the wrapper is alive. The
            # wrapper owns provider-group cleanup and may still replace them.
            # Stability is observed only to avoid mistaking active writes for
            # completion; process exit remains the readiness boundary.
            if stable_since is not None and now - stable_since >= PEER_REPORT_STABLE_SECONDS:
                pass
        else:
            alive = process_identity_matches(process_info) and process_alive(pid)
            now = time.time()
            if now >= deadline:
                return "timeout"
        if not alive:
            return "peer_exited"
        time.sleep(FINISH_WAIT_POLL_SECONDS)


def check_availability(args: argparse.Namespace) -> int:
    availability = load_availability_runtime()
    repo_root = (args.repo_root or default_repo_root()).resolve()
    try:
        availability.bounded_timeout(args.timeout_seconds)
    except ValueError as exc:
        output = availability.result(
            status="unavailable",
            peer=args.peer,
            model=args.model,
            effort=args.effort,
            source="request_contract",
            cli_version=None,
            details=str(exc),
            evidence=str(exc),
        )
    else:
        static = availability.static_failure(args.peer, args.model, args.effort)
        if static is not None:
            output = static
        else:
            try:
                cli_version = require_peer_cli_version(args.peer)
            except SystemExit as exc:
                output = availability.result(
                    status="unknown",
                    peer=args.peer,
                    model=args.model,
                    effort=args.effort,
                    source="cli_preflight",
                    cli_version=None,
                    details=str(exc),
                    evidence=str(exc),
                )
            else:
                output = availability.check(
                    args.peer,
                    args.model,
                    args.effort,
                    cli_version,
                    repo_root,
                    args.timeout_seconds,
                )
    availability.validate_result(output)
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return availability.EXIT_CODES[output["status"]]


def start(args: argparse.Namespace) -> int:
    peer_runtime = load_peer_runtime()
    availability_runtime = load_availability_runtime()
    if peer_runtime.nested_invocation_requested(dict(os.environ)):
        raise SystemExit("nested Agent Collab invocation refused by the cross-agent depth guard")
    repo_root = (args.repo_root or default_repo_root()).resolve()
    run_root = (args.run_root or default_run_root(repo_root)).resolve()
    state = load_state_runtime()
    try:
        state.load_state(run_root)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"invalid v2 Agent Collab state; refusing to launch a peer: {exc}") from exc
    require_workspace_guard_available(repo_root)
    resolved = resolve_settings(repo_root)
    require_valid_settings(resolved)
    settings = resolved["settings"]
    host = args.host
    peer = "claude" if host == "codex" else "codex"
    brief = read_text_arg(args.brief, args.brief_file)
    if not brief:
        raise SystemExit("brief or brief-file is required")
    mode = args.mode or classify_mode(args.target, brief)
    try:
        run_id = require_safe_run_id(args.run_id or utc_run_id(host, mode))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    profile = args.profile or settings["profile"]
    max_local_subagents = args.max_local_subagents if args.max_local_subagents is not None else settings["max_local_subagents"]
    local_subagents_allowed = False if args.no_local_subagents else bool(settings["local_subagents_allowed"])
    online_research = settings["online_research"] if args.online_research is None else args.online_research
    peer_model = args.peer_model if args.peer_model is not None else settings[f"{peer}_model"]
    peer_effort = args.peer_effort if args.peer_effort is not None else settings[f"{peer}_effort"]
    if peer == "claude" and is_fable_model(peer_model) and args.peer_model is None:
        raise SystemExit(
            "Fable is explicit-only; request it for this run with "
            "--peer-model claude-fable-5"
        )
    safe_mode = bool(settings["safe_mode"])
    if type(max_local_subagents) is not int or not 0 <= max_local_subagents <= SAFETY.MAX_LOCAL_SUBAGENTS:
        raise SystemExit(f"max_local_subagents must be between 0 and {SAFETY.MAX_LOCAL_SUBAGENTS}")
    agent_timeout = configured_timeout_seconds(settings["agent_timeout_seconds"])
    # Codex read-only sandboxing uses bubblewrap on Linux. Claude's plan
    # permission mode is provider-native and does not use this backend.
    if safe_mode and peer == "codex":
        sandbox = SAFETY.sandbox_preflight(repo_root, dict(os.environ))
        if sandbox["status"] == "sandbox_unavailable":
            raise SystemExit(
                STRICT_JSON.dumps(
                    {
                        "schema_version": ARTIFACT_SCHEMA_VERSION,
                        "status": "sandbox_unavailable",
                        "peer": peer,
                        "safe_mode": True,
                        "backend": sandbox["backend"],
                        "details": sandbox["details"],
                    }
                )
            )
    peer_cli_version = require_peer_cli_version(peer)
    availability_attestation = availability_runtime.check(
        peer,
        peer_model,
        peer_effort,
        peer_cli_version,
        repo_root,
        DEFAULT_AVAILABILITY_TIMEOUT_SECONDS,
    )
    if availability_attestation["status"] != "available":
        raise SystemExit(
            "requested peer model/effort pair is not attested as available; refusing to create a run:\n"
            + json.dumps(availability_attestation, ensure_ascii=False, indent=2, sort_keys=True)
        )
    availability_runtime.validate_result(availability_attestation, require_available=True)
    request = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "origin": host,
        "host": host,
        "peer": peer,
        "mode": mode,
        "target": args.target,
        "brief": brief,
        "edit_allowed": bool(args.edit_allowed),
        "run_id": run_id,
        "profile": profile,
        "local_subagents_allowed": local_subagents_allowed,
        "max_local_subagents": max_local_subagents,
        "online_research": online_research,
        "safe_mode": safe_mode,
        "peer_model": peer_model,
        "peer_effort": peer_effort,
        "availability_attestation": availability_attestation,
        "peer_timeout_seconds": json_number(agent_timeout),
        "codex_config": list(settings["codex_config"]),
        "claude_tools": settings["claude_tools"],
        "claude_max_turns": int(settings["claude_max_turns"]),
    }
    peer_runtime.validate_request(request)

    run_dir = run_root / run_id
    guard_path = acquire_workspace_guard(repo_root, run_id, host, run_dir)
    run_dir_created = False
    peer_stdout = None
    peer_stderr = None
    process = None
    peer_identity = None
    state_job_created = False
    startup_gate_read = None
    startup_gate_write = None
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
        run_dir_created = True

        request_path = run_dir / "host-request.json"
        write_json(request_path, request)
        run_snapshot(repo_root, run_dir / "before.snapshot", ignored_paths=[run_dir])
        peer_stdout = (run_dir / "peer-report.json").open("w", encoding="utf-8")
        peer_stderr = (run_dir / "peer.stderr.log").open("w", encoding="utf-8")
        env = dict(os.environ)
        env["AGENT_COLLAB_RUN_DIR"] = str(run_dir)
        started_at_epoch = time.time()
        startup_gate_read, startup_gate_write = os.pipe()
        os.set_inheritable(startup_gate_read, True)
        process = subprocess.Popen(
            [
                sys.executable,
                str(runtime_dir() / "peer.py"),
                str(request_path),
                "--repo-root",
                str(repo_root),
                "--raw-output",
                str(run_dir / "peer.raw.json"),
                "--normalization-output",
                str(run_dir / "peer-normalization.json"),
                "--startup-gate-fd",
                str(startup_gate_read),
            ],
            cwd=repo_root,
            stdin=subprocess.DEVNULL,
            stdout=peer_stdout,
            stderr=peer_stderr,
            env=env,
            text=True,
            start_new_session=True,
            pass_fds=(startup_gate_read,),
        )
        try:
            peer_identity = SAFETY.process_identity(process.pid)
        except (OSError, RuntimeError, ValueError) as exc:
            raise RuntimeError(
                "peer runtime exited before its exact process identity could be recorded"
            ) from exc
        process_info = {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "pid": process.pid,
            "pgid": peer_identity["pgid"],
            "process_identity": peer_identity,
            "run_id": run_id,
            "run_dir": str(run_dir),
            "repo_root": str(repo_root),
            "host": host,
            "peer": peer,
            "profile": profile,
            "started_at": utc_timestamp(started_at_epoch),
            "started_at_epoch": started_at_epoch,
            "peer_timeout_seconds": json_number(agent_timeout),
            "peer_cli_version": peer_cli_version,
            "workspace_guard": str(guard_path),
            "settings": {
                "local": resolved["layers"]["local"]["path"],
                "global": resolved["layers"]["global"]["path"],
            },
            "peer_report": str(run_dir / "peer-report.json"),
            "peer_raw": str(run_dir / "peer.raw.json"),
            "peer_normalization": str(run_dir / "peer-normalization.json"),
            "provider_process": str(run_dir / "provider-process.json"),
            "host_first_pass": str(run_dir / "host-first-pass.json"),
        }
        write_json(run_dir / "peer-process.json", process_info)
        update_workspace_guard(
            repo_root,
            run_id,
            phase="peer_running",
            peer_identity=peer_identity,
        )
        state.upsert_job(
            run_root,
            {
                "id": run_id,
                "run_dir": str(run_dir),
                "repo_root": str(repo_root),
                "host": host,
                "peer": peer,
                "mode": mode,
                "target": args.target,
                "status": "running",
                "phase": "peer_running",
                "pid": process.pid,
                "profile": profile,
                "edit_allowed": bool(args.edit_allowed),
                "peer_report": str(run_dir / "peer-report.json"),
                "peer_raw": str(run_dir / "peer.raw.json"),
                "peer_normalization": str(run_dir / "peer-normalization.json"),
                "host_first_pass": str(run_dir / "host-first-pass.json"),
                "peer_stderr": str(run_dir / "peer.stderr.log"),
            },
        )
        state_job_created = True
        os.write(startup_gate_write, b"1")
        os.close(startup_gate_write)
        startup_gate_write = None
        os.close(startup_gate_read)
        startup_gate_read = None
    except BaseException as startup_error:
        for descriptor_name in ("startup_gate_write", "startup_gate_read"):
            descriptor = locals().get(descriptor_name)
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                if descriptor_name == "startup_gate_write":
                    startup_gate_write = None
                else:
                    startup_gate_read = None
        if peer_stdout is not None:
            peer_stdout.close()
        if peer_stderr is not None:
            peer_stderr.close()
        cleanup: dict[str, Any] = {"outcome": "not_started", "quiescent": True}
        if process is not None:
            try:
                if peer_identity is not None:
                    if SAFETY.process_identity_matches(peer_identity):
                        cleanup = SAFETY.terminate_process_group(int(peer_identity["pgid"]))
                    else:
                        group_alive = SAFETY.process_group_alive(int(peer_identity["pgid"]))
                        cleanup = {
                            "outcome": "identity_mismatch_group_live" if group_alive else "identity_mismatch_group_empty",
                            "quiescent": not group_alive,
                            "pgid": int(peer_identity["pgid"]),
                        }
                elif process.poll() is None:
                    # The live Popen handle and a new session prove ownership
                    # even if /proc identity capture failed.
                    cleanup = SAFETY.terminate_process_group(int(process.pid))
                else:
                    group_alive = SAFETY.process_group_alive(int(process.pid))
                    cleanup = {
                        "outcome": "unidentified_group_live" if group_alive else "unidentified_group_empty",
                        "quiescent": not group_alive,
                        "pgid": int(process.pid),
                    }
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    cleanup = {**cleanup, "outcome": "leader_not_reaped", "quiescent": False}
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as cleanup_error:
                cleanup = {
                    "outcome": "cleanup_exception",
                    "quiescent": False,
                    "details": str(cleanup_error),
                }
        if cleanup.get("quiescent") is not True:
            if run_dir_created:
                try:
                    write_json(
                        run_dir / "startup-cleanup-failure.json",
                        {
                            "schema_version": ARTIFACT_SCHEMA_VERSION,
                            "run_id": run_id,
                            "cleanup": cleanup,
                            "startup_error": f"{type(startup_error).__name__}: {startup_error}",
                        },
                    )
                except (OSError, ValueError):
                    pass
            raise RuntimeError(
                "peer startup failed and process-group quiescence could not be proven; "
                f"the workspace guard and run artifacts were retained: {cleanup}"
            ) from startup_error
        try:
            if state_job_created:
                state.remove_job(run_root, run_id)
            release_workspace_guard(repo_root, run_id)
        except (OSError, RuntimeError, ValueError) as rollback_error:
            raise RuntimeError(
                "peer startup process cleanup succeeded but atomic state/guard rollback failed; "
                "run artifacts were retained"
            ) from rollback_error
        if run_dir_created:
            shutil.rmtree(run_dir)
        raise
    finally:
        for descriptor in (startup_gate_write, startup_gate_read):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        if peer_stdout is not None and not peer_stdout.closed:
            peer_stdout.close()
        if peer_stderr is not None and not peer_stderr.closed:
            peer_stderr.close()
    print(json.dumps(process_info, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def load_json(path: Path) -> dict[str, Any]:
    data = STRICT_JSON.load(path, max_bytes=16 * 1024 * 1024)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


class ArtifactValidationError(ValueError):
    pass


class PeerReportRequestMismatch(ArtifactValidationError):
    pass


def validate_claim_shape(
    claim: Any,
    context: str,
    require_source: bool = False,
    allow_extra: bool = False,
) -> None:
    if not isinstance(claim, dict):
        raise ArtifactValidationError(f"{context} claim must be an object; {CLAIM_SHAPE_CONTRACT}")
    for key in ("claim", "status", "evidence"):
        if key not in claim:
            raise ArtifactValidationError(f"{context} claim missing {key}; {CLAIM_SHAPE_CONTRACT}")
        if not isinstance(claim[key], str):
            raise ArtifactValidationError(f"{context} claim {key} must be a string; {CLAIM_SHAPE_CONTRACT}")
    if claim["status"] not in CLAIM_STATUSES:
        raise ArtifactValidationError(f"{context} claim status is invalid; {CLAIM_SHAPE_CONTRACT}")
    if require_source:
        if claim.get("source") not in CLAIM_SOURCES:
            raise ArtifactValidationError(f"{context} claim source is invalid")
    if not allow_extra:
        allowed = {"claim", "status", "evidence"}
        if require_source:
            allowed.add("source")
        unknown = set(claim) - allowed
        if unknown:
            raise ArtifactValidationError(f"{context} claim has unknown keys: {sorted(unknown)}")


def validate_host_first_pass(report: dict[str, Any], request: dict[str, Any]) -> None:
    required = {"schema_version", "run_id", "summary", "claims"}
    missing = required - report.keys()
    if missing:
        raise ArtifactValidationError(f"host-first-pass missing required keys: {sorted(missing)}")
    unknown = set(report) - required
    if unknown:
        raise ArtifactValidationError(f"host-first-pass has unknown keys: {sorted(unknown)}")
    if report["schema_version"] != ARTIFACT_SCHEMA_VERSION:
        raise ArtifactValidationError("host-first-pass schema_version must be 2.0; legacy artifacts are not supported")
    if report["run_id"] != request.get("run_id"):
        raise ArtifactValidationError("host-first-pass run_id does not match host request")
    if not isinstance(report["summary"], str):
        raise ArtifactValidationError("host-first-pass summary must be a string")
    if not isinstance(report["claims"], list):
        raise ArtifactValidationError("host-first-pass claims must be an array")
    for claim in report["claims"]:
        validate_claim_shape(claim, "host-first-pass")


def validate_host_synthesis(report: dict[str, Any], request: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "run_id",
        "summary",
        "verdict",
        "claims",
        "unresolved_risks",
        "workspace_mutation_sha256",
        "final_answer_ready",
    }
    if not isinstance(report, dict) or set(report) != required:
        raise ArtifactValidationError(f"host-synthesis must contain exactly {sorted(required)}")
    if report["schema_version"] != ARTIFACT_SCHEMA_VERSION:
        raise ArtifactValidationError("host-synthesis schema_version must be 2.0; legacy artifacts are not supported")
    if report["run_id"] != request["run_id"]:
        raise ArtifactValidationError("host-synthesis run_id does not match host request")
    if not isinstance(report["summary"], str) or not report["summary"].strip():
        raise ArtifactValidationError("host-synthesis summary must be a non-empty string")
    if report["verdict"] not in RECOMMENDED_VERDICTS:
        raise ArtifactValidationError("host-synthesis verdict is invalid")
    if not isinstance(report["claims"], list):
        raise ArtifactValidationError("host-synthesis claims must be an array")
    for claim in report["claims"]:
        validate_claim_shape(claim, "host-synthesis")
    if not isinstance(report["unresolved_risks"], list) or not all(
        isinstance(item, str) for item in report["unresolved_risks"]
    ):
        raise ArtifactValidationError("host-synthesis unresolved_risks must be an array of strings")
    if re.fullmatch(r"[0-9a-f]{64}", str(report["workspace_mutation_sha256"])) is None:
        raise ArtifactValidationError(
            "host-synthesis workspace_mutation_sha256 must be a lowercase SHA-256 digest"
        )
    if report["final_answer_ready"] is not True:
        raise ArtifactValidationError("host-synthesis final_answer_ready must be true")


def validate_host_result(report: dict[str, Any], request: dict[str, Any], run_dir: Path) -> None:
    if not isinstance(report, dict):
        raise ArtifactValidationError("host-result must be an object")
    phase = report.get("phase")
    if phase == "waiting_for_peer":
        required = {
            "schema_version",
            "run_id",
            "run_dir",
            "status",
            "phase",
            "peer_pid",
            "finish_wait_seconds",
            "finish_timeout_source",
            "peer_report",
            "message",
        }
        if set(report) != required:
            raise ArtifactValidationError(
                "waiting host-result keys must match schema 2.0 exactly; "
                f"missing={sorted(required - set(report))}, unknown={sorted(set(report) - required)}"
            )
        if report["schema_version"] != ARTIFACT_SCHEMA_VERSION or report["run_id"] != request["run_id"]:
            raise ArtifactValidationError("host-result schema_version/run_id does not match the v2 request")
        if Path(str(report["run_dir"])).resolve() != run_dir.resolve():
            raise ArtifactValidationError("host-result run_dir does not match the resolved run directory")
        if report["status"] != "peer_running":
            raise ArtifactValidationError("waiting host-result status must be peer_running")
        if type(report["peer_pid"]) is not int or report["peer_pid"] <= 0:
            raise ArtifactValidationError("waiting host-result peer_pid must be a positive integer")
        if Path(str(report["peer_report"])).resolve() != (run_dir / "peer-report.json").resolve():
            raise ArtifactValidationError("waiting host-result peer_report path is invalid")
        if not isinstance(report["message"], str) or not report["message"].strip():
            raise ArtifactValidationError("waiting host-result message must be a non-empty string")
    elif phase in {"ready_for_synthesis", "done"}:
        common = {
            "schema_version",
            "run_id",
            "run_dir",
            "phase",
            "peer_status",
            "validation_status",
            "finish_wait_seconds",
            "finish_timeout_source",
            "normalization",
            "claim_matrix",
            "adjudicator_report",
            "workspace_mutation",
            "workspace_mutation_sha256",
        }
        required = common | ({"completed_at"} if phase == "done" else set())
        if set(report) != required:
            raise ArtifactValidationError(
                f"host-result keys must match phase {phase} exactly; "
                f"missing={sorted(required - set(report))}, unknown={sorted(set(report) - required)}"
            )
        if report["schema_version"] != ARTIFACT_SCHEMA_VERSION or report["run_id"] != request["run_id"]:
            raise ArtifactValidationError("host-result schema_version/run_id does not match the v2 request")
        if Path(str(report["run_dir"])).resolve() != run_dir.resolve():
            raise ArtifactValidationError("host-result run_dir does not match the resolved run directory")
        if report["peer_status"] not in {"ok", "peer_failed"}:
            raise ArtifactValidationError("host-result peer_status is invalid")
        expected_paths = {
            "normalization": run_dir / "peer-normalization.json",
            "claim_matrix": run_dir / "claim-matrix.json",
            "adjudicator_report": run_dir / "adjudicator-report.json",
        }
        for key, expected in expected_paths.items():
            if not isinstance(report[key], str) or not report[key]:
                raise ArtifactValidationError(f"host-result {key} must be a non-empty string")
            if Path(report[key]).resolve() != expected.resolve():
                raise ArtifactValidationError(f"host-result {key} path does not match the run artifact")
        if not isinstance(report["validation_status"], str) or not report["validation_status"].strip():
            raise ArtifactValidationError("host-result validation_status must be a non-empty string")
        if report["workspace_mutation"] is not None and not isinstance(report["workspace_mutation"], dict):
            raise ArtifactValidationError("host-result workspace_mutation must be an object or null")
        expected_mutation_digest = workspace_mutation_sha256(report["workspace_mutation"])
        if report["workspace_mutation_sha256"] != expected_mutation_digest:
            raise ArtifactValidationError("host-result workspace_mutation_sha256 is invalid")
        if phase == "done" and parse_utc_timestamp(report["completed_at"]) is None:
            raise ArtifactValidationError("host-result completed_at must be an ISO-8601 timestamp")
    else:
        raise ArtifactValidationError(
            "host-result phase must be waiting_for_peer, ready_for_synthesis, or done"
        )
    if not isinstance(report["finish_timeout_source"], str) or not report["finish_timeout_source"].strip():
        raise ArtifactValidationError("host-result finish_timeout_source must be a non-empty string")
    wait_seconds = report["finish_wait_seconds"]
    if (
        not isinstance(wait_seconds, (int, float))
        or isinstance(wait_seconds, bool)
        or not math.isfinite(float(wait_seconds))
        or not MIN_PEER_WAIT_SECONDS <= float(wait_seconds) <= SAFETY.MAX_AGENT_TIMEOUT_SECONDS + FINISH_TIMEOUT_GRACE_SECONDS
    ):
        raise ArtifactValidationError("host-result finish_wait_seconds must be finite and bounded")


def validate_peer_report_matches_request(report: dict[str, Any], request: dict[str, Any]) -> None:
    for key in ("run_id", "origin", "host", "peer", "mode", "target"):
        if report.get(key) != request.get(key):
            raise PeerReportRequestMismatch(f"peer report {key} does not match host request")


def validate_claim_matrix(report: dict[str, Any], request: dict[str, Any]) -> None:
    required = {"schema_version", "run_id", "claims"}
    missing = required - report.keys()
    if missing:
        raise ArtifactValidationError(f"claim-matrix missing required keys: {sorted(missing)}")
    unknown = set(report) - required
    if unknown:
        raise ArtifactValidationError(f"claim-matrix has unknown keys: {sorted(unknown)}")
    if report["schema_version"] != ARTIFACT_SCHEMA_VERSION:
        raise ArtifactValidationError("claim-matrix schema_version must be 2.0; legacy artifacts are not supported")
    if report["run_id"] != request.get("run_id"):
        raise ArtifactValidationError("claim-matrix run_id does not match host request")
    if not isinstance(report["claims"], list):
        raise ArtifactValidationError("claim-matrix claims must be an array")
    for claim in report["claims"]:
        validate_claim_shape(claim, "claim-matrix", require_source=True)


def validate_adjudicator_report(report: dict[str, Any], request: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "run_id",
        "status",
        "summary",
        "false_positives",
        "claims_needing_verification",
        "recommended_verdict",
    }
    missing = required - report.keys()
    if missing:
        raise ArtifactValidationError(f"adjudicator-report missing required keys: {sorted(missing)}")
    unknown = set(report) - (required | {"claims"})
    if unknown:
        raise ArtifactValidationError(f"adjudicator-report has unknown keys: {sorted(unknown)}")
    if report["schema_version"] != ARTIFACT_SCHEMA_VERSION:
        raise ArtifactValidationError("adjudicator-report schema_version must be 2.0; legacy artifacts are not supported")
    if report["run_id"] != request.get("run_id"):
        raise ArtifactValidationError("adjudicator-report run_id does not match host request")
    for key in ("status", "summary", "recommended_verdict"):
        if not isinstance(report[key], str):
            raise ArtifactValidationError(f"adjudicator-report {key} must be a string")
    if report["status"] not in ADJUDICATOR_STATUSES:
        raise ArtifactValidationError("adjudicator-report status is invalid")
    if report["recommended_verdict"] not in RECOMMENDED_VERDICTS:
        raise ArtifactValidationError("adjudicator-report recommended_verdict is invalid")
    for key in ("false_positives", "claims_needing_verification"):
        if not isinstance(report[key], list) or not all(isinstance(item, str) for item in report[key]):
            raise ArtifactValidationError(f"adjudicator-report {key} must be an array of strings")
    if "claims" in report:
        if not isinstance(report["claims"], list):
            raise ArtifactValidationError("adjudicator-report claims must be an array")
        for claim in report["claims"]:
            validate_claim_shape(claim, "adjudicator-report", allow_extra=False)


def claims_with_source(report: dict[str, Any], source: str) -> list[dict[str, Any]]:
    return [
        {**claim, "source": source}
        for claim in report.get("claims", [])
        if isinstance(claim, dict)
    ]


def load_helper_claims(run_dir: Path, request: dict[str, Any]) -> list[dict[str, Any]]:
    path = run_dir / "helper-reports.json"
    if not path.exists():
        return []
    data = STRICT_JSON.load(path, max_bytes=16 * 1024 * 1024)
    required = {"schema_version", "run_id", "reports"}
    if not isinstance(data, dict) or set(data) != required:
        raise ArtifactValidationError(f"helper-reports must contain exactly {sorted(required)}")
    if data["schema_version"] != ARTIFACT_SCHEMA_VERSION:
        raise ArtifactValidationError("helper-reports schema_version must be 2.0; legacy artifacts are not supported")
    if data["run_id"] != request["run_id"]:
        raise ArtifactValidationError("helper-reports run_id does not match host request")
    reports = data["reports"]
    if not isinstance(reports, list):
        raise ArtifactValidationError("helper-reports reports must be an array")
    helper_limit = request["max_local_subagents"] if request["local_subagents_allowed"] else 0
    if len(reports) > helper_limit:
        raise ArtifactValidationError(
            f"helper-reports contains {len(reports)} reports; the resolved per-run limit is {helper_limit}"
        )

    claims: list[dict[str, Any]] = []
    helper_names: set[str] = set()
    for report in reports:
        report_keys = {"name", "summary", "claims"}
        if not isinstance(report, dict) or set(report) != report_keys:
            raise ArtifactValidationError(f"helper report must contain exactly {sorted(report_keys)}")
        if not isinstance(report["name"], str) or not report["name"]:
            raise ArtifactValidationError("helper report name must be a non-empty string")
        if report["name"] in helper_names:
            raise ArtifactValidationError(f"duplicate helper report name: {report['name']}")
        helper_names.add(report["name"])
        if not isinstance(report["summary"], str) or not isinstance(report["claims"], list):
            raise ArtifactValidationError("helper report summary must be a string and claims must be an array")
        for claim in report["claims"]:
            validate_claim_shape(claim, "helper-report")
            claims.append({**claim, "source": "helper"})
    return claims


def build_claim_matrix(
    request: dict[str, Any],
    host_report: dict[str, Any],
    peer_report: dict[str, Any],
    helper_claims: list[dict[str, Any]] | None = None,
    adjudicator_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    claim_matrix = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "run_id": request["run_id"],
        "claims": (
            claims_with_source(host_report, "host")
            + claims_with_source(peer_report, "peer")
            + (helper_claims or [])
            + claims_with_source(adjudicator_report or {}, "adjudicator")
        ),
    }
    validate_claim_matrix(claim_matrix, request)
    return claim_matrix


def default_adjudicator_report(request: dict[str, Any], peer_report: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "run_id": request["run_id"],
        "status": "advisory_pending",
        "summary": "Ultra profile expects a host-local advisory adjudicator after independent reports. This placeholder means no adjudicator artifact was supplied before finish.",
        "false_positives": [],
        "claims_needing_verification": [],
        "recommended_verdict": str(peer_report.get("verdict", "blocked")),
        "claims": [],
    }


def normalize_peer_report_from_artifacts(
    peer_runtime: Any,
    run_dir: Path,
    request: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    peer_report_path = run_dir / "peer-report.json"
    normalization_path = run_dir / "peer-normalization.json"
    last_error: Exception | None = None
    if peer_report_path.exists() and peer_report_path.stat().st_size > 0:
        try:
            normalized = peer_runtime.normalize_json_payload(
                STRICT_JSON.read_text(
                    peer_report_path,
                    max_bytes=16 * 1024 * 1024,
                ).strip(),
                "direct_json",
            )
            peer_runtime.validate_peer_report(normalized.report)
            validate_peer_report_matches_request(normalized.report, request)
        except Exception as exc:
            last_error = exc
        else:
            metadata = dict(normalized.metadata)
            metadata["artifact_source"] = "peer_report"
            metadata["validation_status"] = "ok"
            write_json(normalization_path, metadata)
            write_json(peer_report_path, normalized.report)
            return normalized.report, "ok"

    message = str(last_error) if last_error is not None else "peer report missing or empty"
    if isinstance(last_error, getattr(peer_runtime, "PeerReportValidationError")):
        failure_kind = "schema_validation_failed"
    elif isinstance(last_error, PeerReportRequestMismatch):
        failure_kind = "peer_report_mismatch"
    elif isinstance(last_error, getattr(peer_runtime, "PeerOutputContractError")):
        failure_kind = "noncanonical_output"
    else:
        failure_kind = "invalid_json"
    peer_report = peer_runtime.failure(failure_kind, message, request)
    write_json(peer_report_path, peer_report)
    write_json(
        normalization_path,
        {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "source": "none",
            "artifact_source": "peer_report",
            "input_bytes": peer_report_path.stat().st_size if peer_report_path.exists() else 0,
            "warnings": [],
            "validation_status": failure_kind,
            "error": message,
        },
    )
    return peer_report, failure_kind


def finish(args: argparse.Namespace) -> int:
    repo_root_for_state = (args.repo_root or default_repo_root()).resolve()
    run_root = (args.run_root or default_run_root(repo_root_for_state)).resolve()
    run_dir = resolve_run_reference(repo_root_for_state, args.run, run_root)
    state = load_state_runtime()
    request = require_run_request(run_dir)
    host_first_pass = run_dir / "host-first-pass.json"
    if not host_first_pass.exists():
        raise SystemExit(f"missing required independent host analysis: {host_first_pass}")
    host_report = load_json(host_first_pass)
    try:
        validate_host_first_pass(host_report, request)
    except ArtifactValidationError as exc:
        raise SystemExit(f"invalid independent host analysis {host_first_pass}: {exc}") from exc
    state.upsert_job(run_root, job_patch_from_run(run_dir, "running", "normalizing_peer"))

    process_info = load_json(run_dir / "peer-process.json")
    try:
        validate_peer_process(process_info, request, run_dir)
    except ArtifactValidationError as exc:
        raise SystemExit(f"invalid peer-process artifact in {run_dir}: {exc}") from exc
    repo_root = Path(process_info["repo_root"]).resolve()
    require_workspace_guard_owner(repo_root, request["run_id"])
    _, active_guard = read_workspace_guard(repo_root)
    wrapper_quiescence_proven = bool(
        active_guard is not None
        and active_guard["phase"] == "ready_for_synthesis"
        and active_guard["peer_identity"] == process_info["process_identity"]
    )
    if active_guard is not None and active_guard["phase"] == "ready_for_synthesis" and not wrapper_quiescence_proven:
        raise SystemExit("ready-for-synthesis workspace guard contains an invalid wrapper-quiescence proof")
    pid = int(process_info["pid"])
    peer_report_path = run_dir / "peer-report.json"
    configured_wait_seconds, finish_timeout_source = resolve_finish_wait_timeout(args.timeout_seconds, process_info)
    actual_wait_seconds, hard_deadline = remaining_finish_wait_seconds(
        process_info, configured_wait_seconds
    )
    finish_wait_seconds = configured_wait_seconds
    wait_status = wait_for_peer_report(peer_report_path, process_info, actual_wait_seconds)

    if wait_status == "timeout" and time.time() < hard_deadline:
        output = {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "run_id": request["run_id"],
            "run_dir": str(run_dir),
            "status": "peer_running",
            "phase": "waiting_for_peer",
            "peer_pid": pid,
            "finish_wait_seconds": json_number(finish_wait_seconds),
            "finish_timeout_source": finish_timeout_source,
            "peer_report": str(peer_report_path),
            "message": "Peer is still running and produced no stable complete report before finish timeout.",
        }
        try:
            validate_host_result(output, request, run_dir)
        except ArtifactValidationError as exc:
            raise SystemExit(f"refusing to persist an invalid waiting host result: {exc}") from exc
        state.upsert_job(
            run_root,
            {
                **job_patch_from_run(run_dir, "running", "waiting_for_peer"),
                "pid": pid,
                "finish_wait_seconds": json_number(finish_wait_seconds),
                "finish_timeout_source": finish_timeout_source,
            },
        )
        write_json(run_dir / "host-result.json", output)
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
        return 1

    process_tree_proven = False
    if time.time() >= hard_deadline:
        try:
            require_process_tree_quiescent(
                process_info,
                run_dir,
                request,
                wrapper_quiescence_proven=wrapper_quiescence_proven,
            )
        except ArtifactValidationError:
            cleanup = terminate_tracked_process_tree(process_info, run_dir, request)
            if cleanup["quiescent"] is not True:
                raise SystemExit(
                    "hard peer deadline elapsed but the tracked process tree could not be proven quiescent; "
                    "the workspace guard remains active:\n"
                    + json.dumps(cleanup, ensure_ascii=False, indent=2, sort_keys=True)
                )
            peer_runtime = load_peer_runtime()
            write_json(
                peer_report_path,
                peer_runtime.failure(
                    "timeout",
                    "Peer run exceeded its hard finite deadline.",
                    request,
                    cleanup,
                ),
            )
        process_tree_proven = True
        wait_status = "peer_exited"

    if not process_tree_proven:
        try:
            require_process_tree_quiescent(
                process_info,
                run_dir,
                request,
                wrapper_quiescence_proven=wrapper_quiescence_proven,
            )
        except ArtifactValidationError as exc:
            raise SystemExit(
                f"peer report is not terminal because the tracked process tree is not quiescent: {exc}"
            ) from exc

    if not peer_report_path.exists() or peer_report_path.stat().st_size == 0:
        failure = {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "run_id": request["run_id"],
            "origin": request["origin"],
            "host": request["host"],
            "peer": request["peer"],
            "mode": request["mode"],
            "target": request["target"],
            "status": "peer_failed",
            "verdict": "blocked",
            "summary": "Peer process produced no report before finish timeout.",
            "findings": [],
            "claims": [],
            "limitations": ["Peer report missing or empty."],
            "next_actions": [],
            "error": {
                "kind": "peer_no_output",
                "message": "Peer report missing or empty.",
                "details": None,
            },
        }
        write_json(peer_report_path, failure)

    peer_runtime = load_peer_runtime()
    peer_report, validation_status = normalize_peer_report_from_artifacts(peer_runtime, run_dir, request)

    adjudicator_path = run_dir / "adjudicator-report.json"
    if adjudicator_path.exists():
        adjudicator = load_json(adjudicator_path)
    else:
        adjudicator = default_adjudicator_report(request, peer_report)
        write_json(adjudicator_path, adjudicator)
    try:
        validate_adjudicator_report(adjudicator, request)
    except ArtifactValidationError as exc:
        raise SystemExit(f"invalid adjudicator report {adjudicator_path}: {exc}") from exc

    try:
        helper_claims = load_helper_claims(run_dir, request)
        claim_matrix = build_claim_matrix(request, host_report, peer_report, helper_claims, adjudicator)
    except ArtifactValidationError as exc:
        raise SystemExit(f"invalid helper or claim-matrix artifact in {run_dir}: {exc}") from exc
    write_json(run_dir / "claim-matrix.json", claim_matrix)

    try:
        workspace_mutation = record_host_workspace_mutation(repo_root, run_dir, request)
    except (OSError, ValueError, ArtifactValidationError) as exc:
        raise SystemExit(f"host workspace mutation verification failed: {exc}") from exc
    result = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "run_id": request["run_id"],
        "run_dir": str(run_dir),
        "peer_status": peer_report.get("status"),
        "validation_status": validation_status,
        "finish_wait_seconds": json_number(finish_wait_seconds),
        "finish_timeout_source": finish_timeout_source,
        "normalization": str(run_dir / "peer-normalization.json"),
        "claim_matrix": str(run_dir / "claim-matrix.json"),
        "adjudicator_report": str(run_dir / "adjudicator-report.json"),
        "workspace_mutation": workspace_mutation,
        "workspace_mutation_sha256": workspace_mutation_sha256(workspace_mutation),
    }
    ready_result = {**result, "phase": "ready_for_synthesis"}
    try:
        validate_host_result(ready_result, request, run_dir)
    except ArtifactValidationError as exc:
        raise SystemExit(f"refusing to persist an invalid ready host result: {exc}") from exc
    state.upsert_job(
        run_root,
        {
            **job_patch_from_run(run_dir, "synthesizing", "ready_for_synthesis"),
            "peer_status": peer_report.get("status"),
            "peer_verdict": peer_report.get("verdict"),
            "validation_status": validation_status,
            "normalization_source": (read_json_if_exists(run_dir / "peer-normalization.json") or {}).get("source"),
            "workspace_mutation": bool(workspace_mutation),
            "summary": peer_report.get("summary"),
        },
    )
    update_workspace_guard(
        repo_root,
        request["run_id"],
        phase="ready_for_synthesis",
        peer_identity=process_info["process_identity"],
    )
    write_json(run_dir / "host-result.json", ready_result)
    print(json.dumps(ready_result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def validate_completion_artifacts(
    run_dir: Path,
    request: dict[str, Any],
    host_result: dict[str, Any],
    peer_runtime: Any,
) -> dict[str, Any]:
    peer_report = load_json(run_dir / "peer-report.json")
    host_report = load_json(run_dir / "host-first-pass.json")
    host_synthesis_path = run_dir / "host-synthesis.json"
    if not host_synthesis_path.exists():
        raise ArtifactValidationError(f"missing required host synthesis attestation: {host_synthesis_path}")
    host_synthesis = load_json(host_synthesis_path)
    claim_matrix = load_json(run_dir / "claim-matrix.json")
    adjudicator = load_json(run_dir / "adjudicator-report.json")
    normalization = load_json(run_dir / "peer-normalization.json")
    peer_runtime.validate_peer_report(peer_report)
    validate_peer_report_matches_request(peer_report, request)
    validate_host_first_pass(host_report, request)
    validate_host_synthesis(host_synthesis, request)
    validate_claim_matrix(claim_matrix, request)
    validate_adjudicator_report(adjudicator, request)
    if normalization.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise ArtifactValidationError("peer-normalization schema_version must be 2.0")
    if normalization.get("artifact_source") != "peer_report":
        raise ArtifactValidationError("peer-normalization artifact_source must be peer_report")
    if normalization.get("validation_status") != host_result["validation_status"]:
        raise ArtifactValidationError("peer-normalization validation_status does not match host-result")
    if host_result["peer_status"] != peer_report["status"]:
        raise ArtifactValidationError("host-result peer_status does not match peer-report")
    workspace_mutation = read_json_if_exists(run_dir / "workspace-mutation.json")
    if host_result["workspace_mutation"] != workspace_mutation:
        raise ArtifactValidationError("host-result workspace_mutation does not match the run artifact")
    if host_synthesis["workspace_mutation_sha256"] != host_result["workspace_mutation_sha256"]:
        raise ArtifactValidationError(
            "host-synthesis does not bind the current workspace mutation evidence; inspect the diagnostic and resynthesize"
        )
    return peer_report


def complete(args: argparse.Namespace) -> int:
    repo_root_for_state = (args.repo_root or default_repo_root()).resolve()
    run_root = (args.run_root or default_run_root(repo_root_for_state)).resolve()
    run_dir = resolve_run_reference(repo_root_for_state, args.run, run_root)
    request = require_run_request(run_dir)
    process_info = load_json(run_dir / "peer-process.json")
    try:
        validate_peer_process(process_info, request, run_dir)
    except ArtifactValidationError as exc:
        raise SystemExit(f"invalid peer-process artifact in {run_dir}: {exc}") from exc
    repo_root = Path(process_info["repo_root"]).resolve()

    host_result_path = run_dir / "host-result.json"
    host_result = load_json(host_result_path)
    try:
        validate_host_result(host_result, request, run_dir)
    except ArtifactValidationError as exc:
        raise SystemExit(f"invalid host-result artifact in {run_dir}: {exc}") from exc
    phase = host_result["phase"]
    if phase not in {"ready_for_synthesis", "done"}:
        raise SystemExit("run is not ready to complete; run finish and synthesize the evidence first")
    if phase == "ready_for_synthesis":
        require_workspace_guard_owner(repo_root, request["run_id"])
        _, active_guard = read_workspace_guard(repo_root)
        if (
            active_guard is None
            or active_guard["phase"] != "ready_for_synthesis"
            or active_guard["peer_identity"] != process_info["process_identity"]
        ):
            raise SystemExit(
                "ready-for-synthesis workspace guard does not contain the persisted wrapper-quiescence proof"
            )
        try:
            # finish already proved the wrapper group empty before atomically
            # advancing this owned guard. Never re-query its historical numeric
            # PGID here: it may have been reused during a long synthesis phase.
            require_process_tree_quiescent(
                process_info,
                run_dir,
                request,
                wrapper_quiescence_proven=True,
            )
        except ArtifactValidationError as exc:
            raise SystemExit(
                f"cannot complete while the tracked peer process tree is active: {exc}"
            ) from exc
        try:
            workspace_mutation = record_host_workspace_mutation(repo_root, run_dir, request)
        except (OSError, ValueError, ArtifactValidationError) as exc:
            raise SystemExit(f"final host workspace mutation verification failed: {exc}") from exc
        if host_result["workspace_mutation"] != workspace_mutation:
            host_result = {
                **host_result,
                "workspace_mutation": workspace_mutation,
                "workspace_mutation_sha256": workspace_mutation_sha256(workspace_mutation),
            }
            try:
                validate_host_result(host_result, request, run_dir)
            except ArtifactValidationError as exc:
                raise SystemExit(f"refusing to persist an invalid refreshed host result: {exc}") from exc
            write_json(host_result_path, host_result)
            raise SystemExit(
                "workspace mutation evidence changed after finish; host-result.json was refreshed. "
                "Inspect the diagnostic, resynthesize, copy its workspace_mutation_sha256 into "
                "host-synthesis.json, and rerun complete."
            )
    elif phase == "done":
        _, active_guard = read_workspace_guard(repo_root)
        if active_guard is not None:
            if active_guard["run_id"] != request["run_id"]:
                raise SystemExit(
                    f"Agent Collab workspace guard belongs to run {active_guard['run_id']}, not {request['run_id']}"
                )
            if (
                active_guard["phase"] != "ready_for_synthesis"
                or active_guard["peer_identity"] != process_info["process_identity"]
            ):
                raise SystemExit(
                    "completed run still has a guard without the persisted wrapper-quiescence proof"
                )
            try:
                require_process_tree_quiescent(
                    process_info,
                    run_dir,
                    request,
                    wrapper_quiescence_proven=True,
                )
            except ArtifactValidationError as exc:
                raise SystemExit(
                    f"completed run guard cannot be released while provider state is unproven: {exc}"
                ) from exc

    peer_runtime = load_peer_runtime()
    try:
        peer_report = validate_completion_artifacts(run_dir, request, host_result, peer_runtime)
    except (OSError, ValueError, getattr(peer_runtime, "PeerReportValidationError")) as exc:
        raise SystemExit(f"cannot complete run with invalid v2 synthesis artifacts: {exc}") from exc

    state_status = "completed" if peer_report["status"] == "ok" else "failed"
    completed_at = (
        host_result["completed_at"]
        if phase == "done"
        else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    state = load_state_runtime()
    state.upsert_job(
        run_root,
        {
            **job_patch_from_run(run_dir, state_status, "done"),
            "peer_status": peer_report["status"],
            "peer_verdict": peer_report["verdict"],
            "completed_at": completed_at,
            "summary": peer_report["summary"],
        },
    )

    if phase == "done":
        guard_path, active = read_workspace_guard(repo_root)
        if active is None:
            output = {
                "schema_version": ARTIFACT_SCHEMA_VERSION,
                "run_id": request["run_id"],
                "run_dir": str(run_dir),
                "status": "already_complete",
                "workspace_guard": str(guard_path),
            }
            print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if active["run_id"] != request["run_id"]:
            raise SystemExit(
                f"Agent Collab workspace guard belongs to run {active['run_id']}, not {request['run_id']}"
            )
        release_workspace_guard(repo_root, request["run_id"])
        output = {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "run_id": request["run_id"],
            "run_dir": str(run_dir),
            "status": "already_complete",
            "workspace_guard_released": str(guard_path),
        }
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    done_result = {**host_result, "phase": "done", "completed_at": completed_at}
    try:
        validate_host_result(done_result, request, run_dir)
    except ArtifactValidationError as exc:
        raise SystemExit(f"refusing to persist an invalid completed host result: {exc}") from exc
    write_json(host_result_path, done_result)
    guard_path = release_workspace_guard(repo_root, request["run_id"])
    output = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "run_id": request["run_id"],
        "run_dir": str(run_dir),
        "status": state_status,
        "workspace_guard_released": str(guard_path),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def status(args: argparse.Namespace) -> int:
    repo_root = (args.repo_root or default_repo_root()).resolve()
    run_root = (args.run_root or default_run_root(repo_root)).resolve()
    state = load_state_runtime()

    if args.run:
        run_dir = resolve_run_reference(repo_root, args.run, run_root)
        if args.wait:
            deadline = time.time() + args.timeout_seconds
            while time.time() < deadline:
                summary = summarize_run(run_dir)
                if not summary["peer_alive"]:
                    break
                time.sleep(args.poll_interval_seconds)
        result = summarize_run(run_dir)
        job = state.find_job(run_root, result["run_id"])
        if job:
            result["job"] = job
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    jobs = state.list_jobs(run_root)
    if not args.all:
        jobs = jobs[:8]
    result = {
        "run_root": str(run_root),
        "jobs": jobs,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def result(args: argparse.Namespace) -> int:
    repo_root = (args.repo_root or default_repo_root()).resolve()
    run_root = (args.run_root or default_run_root(repo_root)).resolve()
    run_dir = resolve_run_reference(repo_root, args.run, run_root)
    process_info = read_json_if_exists(run_dir / "peer-process.json")
    output = {
        "run": summarize_run(run_dir),
        "process": process_info,
        "request": read_json_if_exists(run_dir / "host-request.json"),
        "host_first_pass": read_json_if_exists(run_dir / "host-first-pass.json"),
        "host_synthesis": read_json_if_exists(run_dir / "host-synthesis.json"),
        "peer_normalization": read_json_if_exists(run_dir / "peer-normalization.json"),
        "peer_report": read_json_if_exists(run_dir / "peer-report.json"),
        "claim_matrix": read_json_if_exists(run_dir / "claim-matrix.json"),
        "adjudicator_report": read_json_if_exists(run_dir / "adjudicator-report.json"),
        "host_result": read_json_if_exists(run_dir / "host-result.json"),
        "workspace_mutation": read_json_if_exists(run_dir / "workspace-mutation.json"),
        "peer_stderr_tail": stderr_tail(run_dir / "peer.stderr.log"),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def is_direct_run_dir(run_root: Path, run_dir: Path) -> bool:
    try:
        resolved_root = run_root.resolve()
        resolved_run = run_dir.resolve()
    except FileNotFoundError:
        return False
    return resolved_run.parent == resolved_root and any((resolved_run / name).exists() for name in RUN_ARTIFACT_NAMES)


def run_dir_timestamp(run_dir: Path) -> str:
    try:
        timestamp = run_dir.stat().st_mtime
    except FileNotFoundError:
        timestamp = 0
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def history_candidates(run_root: Path, state_runtime: Any) -> list[dict[str, Any]]:
    loaded_state = state_runtime.load_state(run_root)
    jobs = [job for job in loaded_state.get("jobs", []) if isinstance(job, dict)]
    jobs_by_id = {str(job.get("id")): job for job in jobs if job.get("id")}
    candidates: dict[str, dict[str, Any]] = {}

    if run_root.exists():
        for run_dir in run_root.iterdir():
            if not run_dir.is_dir() or not is_direct_run_dir(run_root, run_dir):
                continue
            run_id = run_dir.name
            job = jobs_by_id.get(run_id, {})
            updated_at = str(job.get("updated_at") or job.get("created_at") or run_dir_timestamp(run_dir))
            candidates[run_id] = {
                "run_id": run_id,
                "run_dir": str(run_dir),
                "status": str(job.get("status") or "orphan"),
                "updated_at": updated_at,
                "sort_key": updated_at,
            }

    for job in jobs:
        run_id = str(job.get("id") or "")
        run_dir_text = str(job.get("run_dir") or "")
        if not run_id or not run_dir_text:
            continue
        run_dir = Path(run_dir_text)
        if not run_dir.exists() or run_id in candidates or not is_direct_run_dir(run_root, run_dir):
            continue
        updated_at = str(job.get("updated_at") or job.get("created_at") or run_dir_timestamp(run_dir))
        candidates[run_id] = {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "status": str(job.get("status") or "unknown"),
            "updated_at": updated_at,
            "sort_key": updated_at,
        }

    return sorted(candidates.values(), key=lambda item: (str(item["sort_key"]), str(item["run_id"])), reverse=True)


def delete_history_candidates(run_root: Path, state_runtime: Any, candidates: list[dict[str, Any]], dry_run: bool) -> list[dict[str, Any]]:
    deleted: list[dict[str, Any]] = []
    for candidate in candidates:
        run_dir = Path(candidate["run_dir"])
        if not is_direct_run_dir(run_root, run_dir):
            continue
        deleted.append({key: candidate[key] for key in ("run_id", "run_dir", "status", "updated_at")})
        if not dry_run:
            shutil.rmtree(run_dir)

    if deleted and not dry_run:
        deleted_ids = {item["run_id"] for item in deleted}
        deleted_dirs = {item["run_dir"] for item in deleted}
        with state_runtime.state_lock(run_root):
            loaded_state = state_runtime.load_state(run_root)
            loaded_state["jobs"] = [
                job
                for job in loaded_state.get("jobs", [])
                if isinstance(job, dict)
                and str(job.get("id")) not in deleted_ids
                and str(job.get("run_dir")) not in deleted_dirs
            ]
            state_runtime.save_state(run_root, loaded_state)
    return deleted


def resolve_history_retain(args: argparse.Namespace, default_retained: int = DEFAULT_HISTORY_RETAINED_RUNS) -> int:
    if getattr(args, "all", False):
        return 0
    if getattr(args, "retain", None) is not None:
        return max(int(args.retain), 0)
    return max(int(default_retained), 0)


def build_clear_history_output(args: argparse.Namespace, default_retained: int = DEFAULT_HISTORY_RETAINED_RUNS) -> tuple[int, dict[str, Any] | None]:
    repo_root = (args.repo_root or default_repo_root()).resolve()
    run_root = (args.run_root or default_run_root(repo_root)).resolve()
    state_runtime = load_state_runtime()
    active_statuses = state_runtime.ACTIVE_STATUSES
    candidates = history_candidates(run_root, state_runtime)
    _, active_guard = read_workspace_guard(repo_root)
    guarded_run_id = str(active_guard["run_id"]) if active_guard is not None else None
    guarded_run_dir = (
        str(Path(str(active_guard["run_dir"])).resolve()) if active_guard is not None else None
    )

    def is_guarded(candidate: dict[str, Any]) -> bool:
        return bool(
            active_guard is not None
            and (
                candidate["run_id"] == guarded_run_id
                or str(Path(candidate["run_dir"]).resolve()) == guarded_run_dir
            )
        )

    if getattr(args, "run", None):
        try:
            target_dir = resolve_run_reference(repo_root, args.run, run_root)
            target_id = target_dir.name
        except SystemExit:
            target_id = str(args.run)
        selected = [candidate for candidate in candidates if candidate["run_id"] == target_id]
        retained = [candidate for candidate in candidates if candidate["run_id"] != target_id]
    else:
        terminal = [candidate for candidate in candidates if candidate["status"] not in active_statuses]
        active = [candidate for candidate in candidates if candidate["status"] in active_statuses]
        retain = resolve_history_retain(args, default_retained)
        retained_terminal = terminal[:retain]
        selected = terminal[retain:]
        retained = active + retained_terminal

    guarded_preserved = [candidate for candidate in selected if is_guarded(candidate)]
    if guarded_preserved:
        retained.extend(candidate for candidate in guarded_preserved if candidate not in retained)
    active_preserved = [
        candidate
        for candidate in selected
        if candidate["status"] in active_statuses or is_guarded(candidate)
    ]
    all_active_preserved = [candidate for candidate in candidates if candidate["status"] in active_statuses]
    for candidate in guarded_preserved:
        if candidate not in all_active_preserved:
            all_active_preserved.append(candidate)
    deletable = [
        candidate
        for candidate in selected
        if candidate["status"] not in active_statuses and not is_guarded(candidate)
    ]
    if deletable and not args.dry_run and not args.yes:
        print("clear-history requires --yes for deletion; rerun with --dry-run to preview", file=sys.stderr)
        return 2, None

    deleted = delete_history_candidates(run_root, state_runtime, deletable, args.dry_run)
    output = {
        "action": "clear-history",
        "dry_run": bool(args.dry_run),
        "run_root": str(run_root),
        "deleted": deleted,
        "retained": [{key: candidate[key] for key in ("run_id", "run_dir", "status", "updated_at")} for candidate in retained],
        "active_preserved": [
            {key: candidate[key] for key in ("run_id", "run_dir", "status", "updated_at")}
            for candidate in (active_preserved or all_active_preserved)
        ],
        "missing": [],
        "skipped": [],
    }
    return 0, output


def clear_history(args: argparse.Namespace) -> int:
    repo_root = (args.repo_root or default_repo_root()).resolve()
    resolved = resolve_settings(repo_root)
    require_valid_settings(resolved)
    settings = resolved["settings"]
    status, output = build_clear_history_output(args, int(settings.get("history_retained_runs", DEFAULT_HISTORY_RETAINED_RUNS)))
    if output is not None:
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return status


def terminate_tracked_process_tree(
    process_info: dict[str, Any],
    run_dir: Path,
    request: dict[str, Any],
) -> dict[str, Any]:
    try:
        provider = load_provider_process(run_dir, request)
    except (OSError, ValueError, ArtifactValidationError) as exc:
        return {
            "quiescent": False,
            "provider": {"outcome": "invalid_tracker", "details": str(exc)},
            "wrapper": {"outcome": "not_attempted"},
        }

    if provider["status"] == "pending":
        provider_cleanup = {"outcome": "not_started", "quiescent": True}
    elif provider["status"] == "quiescent":
        provider_cleanup = {
            "outcome": "already_quiescent",
            "quiescent": True,
        }
    elif (
        provider["status"] == "cleanup_failed"
        and provider["cleanup_outcome"] == "capture_channel_not_quiescent"
    ):
        # A descendant escaped the provider's process group while retaining a
        # capture channel. The original PGID can never prove it exited.
        provider_cleanup = {
            "outcome": "capture_channel_not_quiescent",
            "quiescent": False,
        }
    elif (
        provider["process_identity"] is not None
        and SAFETY.process_identity_matches(provider["process_identity"])
    ):
        provider_pgid = int(provider["pgid"])
        provider_cleanup = SAFETY.terminate_process_group(provider_pgid)
        if provider_cleanup["quiescent"] and provider["process_identity"] is not None:
            provider = {
                **provider,
                "status": "quiescent",
                "cleanup_outcome": str(provider_cleanup["outcome"]),
                "completed_at": utc_timestamp(),
            }
            write_json(run_dir / "provider-process.json", provider)
    else:
        provider_group_alive = SAFETY.process_group_alive(int(provider["pgid"]))
        if provider_group_alive:
            # The recorded identity is gone or different, so the numeric PGID
            # may belong to an unrelated process. Fail closed without signaling.
            provider_cleanup = {
                "outcome": "identity_mismatch_group_live",
                "quiescent": False,
            }
        else:
            # An empty recorded group is sufficient terminal proof. Persist it
            # once so later transitions never re-query a reusable numeric PGID.
            provider_cleanup = {
                "outcome": "identity_mismatch_group_empty",
                "quiescent": True,
            }
            provider = {
                **provider,
                "status": "quiescent",
                "cleanup_outcome": provider_cleanup["outcome"],
                "completed_at": utc_timestamp(),
            }
            write_json(run_dir / "provider-process.json", provider)

    wrapper_pgid = int(process_info["pgid"])
    if process_identity_matches(process_info):
        wrapper_cleanup = SAFETY.terminate_process_group(wrapper_pgid)
    else:
        wrapper_cleanup = {
            "outcome": "identity_mismatch",
            "quiescent": not SAFETY.process_group_alive(wrapper_pgid),
        }
    if wrapper_cleanup.get("quiescent"):
        try:
            refreshed_provider = load_provider_process(run_dir, request)
        except (OSError, ValueError, ArtifactValidationError):
            refreshed_provider = provider
        if refreshed_provider["status"] in {"pending", "quiescent"}:
            provider = refreshed_provider
            provider_cleanup = {
                "outcome": "wrapper_forwarded_cleanup",
                "quiescent": True,
            }
        elif refreshed_provider["status"] in {"running", "cleanup_failed"}:
            provider = refreshed_provider
            if provider_cleanup.get("quiescent") is not False:
                provider_cleanup = {
                    "outcome": "provider_not_quiescent_after_wrapper_cleanup",
                    "quiescent": False,
                }
    quiescent = bool(
        provider_cleanup.get("quiescent")
        and wrapper_cleanup.get("quiescent")
        and (
            provider["status"] == "pending"
            or provider["status"] == "quiescent"
        )
    )
    return {
        "quiescent": quiescent,
        "provider": provider_cleanup,
        "wrapper": wrapper_cleanup,
    }


def process_identity_matches(process_info: dict[str, Any]) -> bool:
    return SAFETY.process_identity_matches(process_info.get("process_identity"))


def cancel(args: argparse.Namespace) -> int:
    repo_root = (args.repo_root or default_repo_root()).resolve()
    run_root = (args.run_root or default_run_root(repo_root)).resolve()
    run_dir = resolve_run_reference(repo_root, args.run, run_root)
    process_info = load_json(run_dir / "peer-process.json")
    request = require_run_request(run_dir)
    try:
        validate_peer_process(process_info, request, run_dir)
    except ArtifactValidationError as exc:
        raise SystemExit(f"invalid peer-process artifact in {run_dir}: {exc}") from exc
    peer_repo_root = Path(process_info["repo_root"]).resolve()
    require_workspace_guard_owner(peer_repo_root, request["run_id"])
    pid = int(process_info["pid"])
    force_before_min_wait = bool(getattr(args, "force_before_min_wait", False))
    reason = str(getattr(args, "reason", "") or "").strip()
    if force_before_min_wait and not reason:
        raise SystemExit("--reason is required with --force-before-min-wait")

    identity_matches = process_identity_matches(process_info)
    peer_alive = process_alive(pid) if identity_matches else False
    elapsed_seconds = peer_elapsed_seconds(process_info, run_dir)
    before_minimum_wait = (
        peer_alive
        and elapsed_seconds is not None
        and elapsed_seconds < MIN_PEER_WAIT_SECONDS
    )
    if before_minimum_wait and not force_before_min_wait:
        output = {
            "run_id": request["run_id"],
            "run_dir": str(run_dir),
            "pid": pid,
            "outcome": "refused",
            "reason": "minimum_wait_not_elapsed",
            "elapsed_seconds": json_number(elapsed_seconds),
            "minimum_wait_seconds": MIN_PEER_WAIT_SECONDS,
            "minimum_wait_remaining_seconds": json_number(MIN_PEER_WAIT_SECONDS - elapsed_seconds),
            "message": "Peer is still inside the minimum wait window; use finish/status and keep waiting. Empty peer-report.json or stderr is normal while the peer is alive.",
        }
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
        return 1

    cleanup = terminate_tracked_process_tree(process_info, run_dir, request)
    if cleanup["quiescent"] is not True:
        raise SystemExit(
            "refusing to release the workspace guard because the tracked process tree is not quiescent:\n"
            + json.dumps(cleanup, ensure_ascii=False, indent=2, sort_keys=True)
        )
    outcome = "cancelled_quiescent"
    cancel_details = {
        "forced": force_before_min_wait,
        "reason": reason or ("minimum_wait_elapsed" if not before_minimum_wait else "unspecified"),
        "elapsed_seconds": json_number(elapsed_seconds),
        "minimum_wait_seconds": MIN_PEER_WAIT_SECONDS,
        "before_minimum_wait": before_minimum_wait,
    }
    peer_report_path = run_dir / "peer-report.json"
    if peer_report_path.exists() and peer_report_path.stat().st_size > 0:
        preserved = run_dir / "peer.cancelled.raw"
        try:
            with peer_report_path.open("rb") as source, preserved.open("xb") as destination:
                destination.write(source.read(16 * 1024 * 1024 + 1))
        except FileExistsError:
            # A prior cancellation attempt owns the original evidence. Never
            # replace it with a generated cancellation report on retry.
            pass
    peer_runtime = load_peer_runtime()
    write_json(
        peer_report_path,
        peer_runtime.failure(
            "cancelled",
            "Peer run was cancelled by host.",
            request,
            {**cancel_details, "process_cleanup": cleanup},
        ),
    )
    try:
        record_host_workspace_mutation(peer_repo_root, run_dir, request)
    except (OSError, ValueError, ArtifactValidationError) as exc:
        raise SystemExit(
            f"refusing to release the workspace guard because mutation verification failed: {exc}"
        ) from exc
    state = load_state_runtime()
    state.upsert_job(
        run_root,
        {
            **job_patch_from_run(run_dir, "cancelled", "cancelled"),
            "pid": None,
            "completed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "cancel": cancel_details,
        },
    )
    guard_path = release_workspace_guard(peer_repo_root, request["run_id"])
    output = {
        "run_id": request["run_id"],
        "run_dir": str(run_dir),
        "pid": pid,
        "outcome": outcome,
        "forced": force_before_min_wait,
        "reason": reason or cancel_details["reason"],
        "elapsed_seconds": json_number(elapsed_seconds),
        "minimum_wait_seconds": MIN_PEER_WAIT_SECONDS,
        "workspace_guard_released": str(guard_path),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def settings_from_args(args: argparse.Namespace) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    mapping = {
        "profile": "profile",
        "local_subagents_allowed": "local_subagents_allowed",
        "max_local_subagents": "max_local_subagents",
        "safe_mode": "safe_mode",
        "codex_model": "codex_model",
        "codex_effort": "codex_effort",
        "online_research": "online_research",
        "codex_config": "codex_config",
        "claude_model": "claude_model",
        "claude_effort": "claude_effort",
        "claude_tools": "claude_tools",
        "claude_max_turns": "claude_max_turns",
        "history_retained_runs": "history_retained_runs",
    }
    for attr, key in mapping.items():
        value = getattr(args, attr, None)
        if value is not None:
            updates[key] = value
    if getattr(args, "timeout_seconds", None) is not None:
        updates["agent_timeout_seconds"] = args.timeout_seconds
    return normalize_settings(updates, "setup arguments")


def choose_option(label: str, current: str, options: list[str]) -> str:
    while True:
        print(f"{label} [{current}]")
        for index, option in enumerate(options, start=1):
            marker = " (current)" if option == current else ""
            print(f"  {index}. {option}{marker}")
        answer = input("> ").strip()
        if not answer:
            return current
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1]
        if answer in options:
            return answer
        print(f"Choose one of: {', '.join(options)}")


def choose_text(label: str, current: str) -> str:
    answer = input(f"{label} [{current}]\n> ").strip()
    return answer or current


def choose_codex_config(current: list[str]) -> list[str]:
    current_label = ", ".join(current) if current else "none"
    answer = input(f"Additional Codex config key=value entries [{current_label}]\n> ").strip()
    if not answer:
        return current
    if answer.lower() in {"none", "clear", "-"}:
        return []
    return [item.strip() for item in answer.split(",") if item.strip()]


def choose_bool(label: str, current: bool) -> bool:
    suffix = "Y/n" if current else "y/N"
    while True:
        answer = input(f"{label} [{suffix}]\n> ").strip().lower()
        if not answer:
            return current
        if answer in {"y", "yes", "true", "1"}:
            return True
        if answer in {"n", "no", "false", "0"}:
            return False
        print("Choose yes or no.")


def choose_timeout(current: str) -> str:
    labels = {
        "2700": "45 minutes",
        "3600": "60 minutes",
    }
    while True:
        current_label = labels.get(current, f"{current} seconds")
        print(f"Peer timeout [{current_label}]")
        print("  1. 45 minutes")
        print("  2. 60 minutes")
        print("  3. custom seconds")
        answer = input("> ").strip()
        if not answer:
            return current
        if answer == "1":
            return "2700"
        if answer == "2":
            return "3600"
        if answer == "3":
            return normalize_timeout(input("Seconds\n> ").strip())
        try:
            return normalize_timeout(answer)
        except ValueError:
            print("Choose an option or enter a positive number of seconds.")


def interactive_setup(current: dict[str, Any]) -> dict[str, Any]:
    settings = dict(current)
    settings["profile"] = choose_option("Profile", settings["profile"], ["ultra", "max", "standard"])
    full_permission = not bool(settings["safe_mode"])
    full_permission = choose_bool("Use full peer permissions by default", full_permission)
    settings["safe_mode"] = not full_permission
    settings["agent_timeout_seconds"] = choose_timeout(str(settings["agent_timeout_seconds"]))
    settings["history_retained_runs"] = int(choose_text("Terminal run history retained", str(settings["history_retained_runs"])))
    settings["local_subagents_allowed"] = choose_bool("Allow local subagents", bool(settings["local_subagents_allowed"]))
    settings["max_local_subagents"] = int(choose_text("Maximum local subagents", str(settings["max_local_subagents"])))
    settings["codex_model"] = choose_text("Codex peer model", str(settings["codex_model"]))
    settings["codex_effort"] = choose_text(
        "Codex reasoning effort (validated against the live model catalog at launch)",
        str(settings["codex_effort"]),
    )
    settings["online_research"] = choose_bool("Allow online research", bool(settings["online_research"]))
    settings["codex_config"] = choose_codex_config(list(settings["codex_config"]))
    settings["claude_model"] = choose_text("Claude peer model", str(settings["claude_model"]))
    settings["claude_effort"] = choose_text(
        "Claude effort (low, medium, high, xhigh, max, or ultracode orchestration)",
        str(settings["claude_effort"]),
    )
    settings["claude_tools"] = choose_text("Claude tool access (`default` or --tools list)", str(settings["claude_tools"]))
    settings["claude_max_turns"] = choose_text("Claude max turns", str(settings["claude_max_turns"]))
    return normalize_settings(settings, "interactive setup")


def reset_settings(args: argparse.Namespace, repo_root: Path) -> int:
    scopes = ["local", "global"] if args.reset == "all" else [args.reset]
    removed: list[str] = []
    missing: list[str] = []
    for scope in scopes:
        path = settings_path(scope, repo_root)
        if path.exists():
            removed.append(str(path))
            if not args.dry_run:
                path.unlink()
        else:
            missing.append(str(path))
    output = {
        "action": "reset",
        "dry_run": bool(args.dry_run),
        "reset": args.reset,
        "removed": removed,
        "missing": missing,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def setup(args: argparse.Namespace) -> int:
    repo_root = (args.repo_root or default_repo_root()).resolve()
    if args.reset:
        return reset_settings(args, repo_root)

    resolved = resolve_settings(repo_root, env={})
    require_valid_settings(resolved)
    settings = dict(resolved["settings"])
    updates = settings_from_args(args)
    settings.update(updates)
    should_clear_history = bool(getattr(args, "clear_history", False))
    if not args.no_input:
        try:
            settings = interactive_setup(settings)
            should_clear_history = should_clear_history or choose_bool("Clear old run history now", False)
        except EOFError:
            print("setup requires interactive stdin; rerun with --no-input for non-interactive use", file=sys.stderr)
            return 2
    if should_clear_history and args.no_input and not args.dry_run and not getattr(args, "yes", False):
        print("setup --clear-history requires --yes for deletion; rerun with --dry-run to preview", file=sys.stderr)
        return 2
    settings = normalize_settings(settings, "setup")
    path = settings_path(args.scope, repo_root)
    if not args.dry_run:
        write_settings_file(path, settings)
    history_cleanup = None
    if should_clear_history:
        cleanup_args = argparse.Namespace(
            repo_root=repo_root,
            run_root=default_run_root(repo_root),
            run=None,
            retain=settings["history_retained_runs"],
            all=False,
            dry_run=args.dry_run,
            yes=getattr(args, "yes", False),
        )
        cleanup_status, history_cleanup = build_clear_history_output(cleanup_args, int(settings["history_retained_runs"]))
        if cleanup_status != 0:
            return cleanup_status
    output = {
        "action": "setup",
        "dry_run": bool(args.dry_run),
        "scope": args.scope,
        "settings_path": str(path),
        "settings": settings,
        "precedence": "environment > local > global > built-in",
    }
    if history_cleanup is not None:
        output["history_cleanup"] = history_cleanup
    if args.print_env:
        output["env"] = settings_to_env(settings)
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def run_command_capture(command: list[str], timeout: float = 20) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        return {
            "available": True,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    except FileNotFoundError as exc:
        return {"available": False, "returncode": None, "stdout": "", "stderr": str(exc)}
    except subprocess.TimeoutExpired as exc:
        return {
            "available": True,
            "returncode": None,
            "stdout": exc.stdout or "",
            "stderr": str(exc),
            "timeout": True,
        }


def parse_json_command(command: list[str], timeout: float = 20) -> dict[str, Any]:
    result = run_command_capture(command, timeout)
    data: Any = None
    error: str | None = None
    if result["stdout"]:
        try:
            data = STRICT_JSON.loads(result["stdout"], max_bytes=16 * 1024 * 1024)
        except ValueError as exc:
            error = str(exc)
    return {**result, "json": data, "json_error": error}


def summarize_claude_auth(report: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {}
    return {
        "logged_in": report.get("loggedIn"),
        "auth_method": report.get("authMethod"),
        "api_provider": report.get("apiProvider"),
        "subscription_type": report.get("subscriptionType"),
    }


def doctor(args: argparse.Namespace) -> int:
    repo_root = (args.repo_root or default_repo_root()).resolve()
    run_root = (args.run_root or default_run_root(repo_root)).resolve()
    peer_runtime = load_peer_runtime()
    resolved = resolve_settings(repo_root)
    settings = resolved["settings"]
    codex_path = shutil.which("codex")
    claude_path = shutil.which("claude")
    codex_auth = run_command_capture(["codex", "login", "status"], timeout=10) if codex_path else {}
    claude_auth = parse_json_command(["claude", "auth", "status", "--json"], timeout=10) if claude_path else {}
    claude_report = claude_auth.get("json") if isinstance(claude_auth, dict) else None
    settings_errors = settings_error_messages(resolved)
    state_runtime = load_state_runtime()
    try:
        state_data = state_runtime.load_state(run_root)
    except (OSError, ValueError) as exc:
        state_check = {
            "ok": False,
            "value": str(run_root / "state.json"),
            "error": str(exc),
        }
    else:
        state_check = {
            "ok": True,
            "value": str(run_root / "state.json"),
            "jobs": len(state_data["jobs"]),
        }

    def cli_version_check(name: str, path: str | None) -> dict[str, Any]:
        minimum = MIN_CLI_VERSIONS[name]
        result = run_command_capture([name, "--version"], timeout=10) if path else {}
        raw = f"{result.get('stdout', '')}\n{result.get('stderr', '')}".strip()
        parsed = parse_cli_version(raw)
        return {
            "ok": path is not None and parsed is not None and parsed >= minimum,
            "value": {
                "path": path,
                "version": ".".join(str(part) for part in parsed) if parsed else None,
                "minimum": ".".join(str(part) for part in minimum),
            },
        }

    checks = {
        "repo_root": {"ok": repo_root.exists(), "value": str(repo_root)},
        "run_root": {"ok": run_root.exists() or path_is_creatable(run_root), "value": str(run_root)},
        "python": {"ok": sys.version_info >= (3, 10), "value": sys.version.split()[0]},
        "git": {"ok": shutil.which("git") is not None, "value": shutil.which("git")},
        "claude_cli": cli_version_check("claude", claude_path),
        "codex_cli": cli_version_check("codex", codex_path),
        "settings": {
            "ok": not settings_errors,
            "value": {"local": resolved["layers"]["local"]["path"], "global": resolved["layers"]["global"]["path"]},
            "errors": settings_errors,
        },
        "peer_schema": {"ok": peer_runtime.default_schema_path().exists(), "value": str(peer_runtime.default_schema_path())},
        "timeout_floor_seconds": {"ok": peer_runtime.MIN_AGENT_TIMEOUT_SECONDS >= 2700, "value": peer_runtime.MIN_AGENT_TIMEOUT_SECONDS},
        "state_file": state_check,
        "effective_configuration": {
            "ok": not settings_errors,
            "value": {
                "codex": {"model": settings["codex_model"], "effort": settings["codex_effort"]},
                "claude": {"model": settings["claude_model"], "effort": settings["claude_effort"]},
                "online_research": settings["online_research"],
                "uses_built_in_model_effort_defaults": (
                    settings["codex_model"] == "gpt-5.6-sol"
                    and settings["codex_effort"] == "max"
                    and settings["claude_model"] == "claude-opus-4-8"
                    and settings["claude_effort"] == "max"
                ),
            },
        },
        "codex_auth": {
            "ok": bool(codex_path) and codex_auth.get("returncode") == 0,
            "value": str(codex_auth.get("stdout") or codex_auth.get("stderr") or "").strip(),
            "returncode": codex_auth.get("returncode") if isinstance(codex_auth, dict) else None,
        },
        "claude_auth": {
            "ok": bool(claude_path) and isinstance(claude_report, dict) and claude_report.get("loggedIn") is True,
            "value": summarize_claude_auth(claude_report),
            "returncode": claude_auth.get("returncode") if isinstance(claude_auth, dict) else None,
        },
        "current_contract": {
            "ok": True,
            "value": {
                "codex": ["--ask-for-approval never", "exec --strict-config", "--output-schema", "--output-last-message"],
                "claude": ["--permission-mode", "--tools", "--disallowedTools", "--json-schema", "--output-format json"],
            },
        },
    }
    output = {
        "ok": all(item["ok"] for item in checks.values()),
        "checks": checks,
        "settings": settings,
        "setting_sources": resolved["sources"],
        "precedence": "environment > local > global > built-in",
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if output["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Agent Collab peer-first host run artifacts.")
    sub = parser.add_subparsers(dest="command", required=True)

    start_parser = sub.add_parser("start", help="Create a run directory and launch the cross-agent peer.")
    start_parser.add_argument("--host", choices=sorted(ORIGINS), required=True)
    start_parser.add_argument("--mode", choices=sorted(MODES))
    start_parser.add_argument("--target", required=True)
    start_parser.add_argument("--brief")
    start_parser.add_argument("--brief-file", type=Path)
    start_parser.add_argument("--edit-allowed", action="store_true")
    start_parser.add_argument("--profile", choices=["standard", "max", "ultra"])
    start_parser.add_argument("--peer-model", help="Override the resolved peer model for this run.")
    start_parser.add_argument(
        "--peer-effort",
        help="Override the resolved peer effort; live availability validation is authoritative.",
    )
    start_parser.add_argument(
        "--online-research",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override online research for this run.",
    )
    start_parser.add_argument("--max-local-subagents", type=int)
    start_parser.add_argument("--no-local-subagents", action="store_true")
    start_parser.add_argument("--run-id")
    start_parser.add_argument("--run-root", type=Path)
    start_parser.add_argument("--repo-root", type=Path)
    start_parser.set_defaults(func=start)

    availability_parser = sub.add_parser(
        "check-availability",
        help="Check one exact peer model/effort pair and print a strict JSON result.",
    )
    availability_parser.add_argument("--peer", choices=sorted(ORIGINS), required=True)
    availability_parser.add_argument("--model", required=True)
    availability_parser.add_argument("--effort", required=True)
    availability_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_AVAILABILITY_TIMEOUT_SECONDS,
        help=f"Bound the provider metadata check to at most {MAX_AVAILABILITY_TIMEOUT_SECONDS:g} seconds.",
    )
    availability_parser.add_argument("--repo-root", type=Path)
    availability_parser.set_defaults(func=check_availability)

    setup_parser = sub.add_parser("setup", help="Configure Agent Collab local or global peer defaults.")
    setup_parser.add_argument("--scope", choices=["local", "global"], default="local")
    setup_parser.add_argument("--no-input", action="store_true", help="Do not prompt; write provided flags over current defaults.")
    setup_parser.add_argument("--dry-run", action="store_true", help="Show changes without writing or removing settings files.")
    setup_parser.add_argument("--print-env", action="store_true", help="Include equivalent environment variables in output.")
    setup_parser.add_argument("--reset", choices=sorted(RESET_SCOPES), help="Remove local, global, or all Agent Collab settings.")
    setup_parser.add_argument("--profile", choices=sorted(PROFILE_CHOICES))
    setup_parser.add_argument("--local-subagents-allowed", action=argparse.BooleanOptionalAction)
    setup_parser.add_argument("--max-local-subagents", type=int)
    setup_parser.add_argument("--timeout-seconds", type=float)
    setup_parser.add_argument("--history-retained-runs", type=int)
    setup_parser.add_argument("--clear-history", action="store_true", help="Clear old run history after setup.")
    setup_parser.add_argument("--yes", action="store_true", help="Confirm setup actions that delete run history.")
    setup_parser.add_argument("--safe-mode", action=argparse.BooleanOptionalAction)
    setup_parser.add_argument("--codex-model")
    setup_parser.add_argument("--codex-effort")
    setup_parser.add_argument("--online-research", action=argparse.BooleanOptionalAction)
    setup_parser.add_argument(
        "--codex-config",
        action="append",
        metavar="key=value",
        help="Additional Codex config override to pass as `codex exec -c key=value`; repeat for multiple entries.",
    )
    setup_parser.add_argument("--claude-model")
    setup_parser.add_argument("--claude-effort")
    setup_parser.add_argument("--claude-tools")
    setup_parser.add_argument("--claude-max-turns")
    setup_parser.add_argument("--repo-root", type=Path)
    setup_parser.set_defaults(func=setup)

    finish_parser = sub.add_parser("finish", help="Validate peer output and write synthesis support artifacts.")
    finish_parser.add_argument("run", help="Run ID, unique prefix, or run directory.")
    finish_parser.add_argument("--timeout-seconds", type=float, default=None)
    finish_parser.add_argument("--run-root", type=Path)
    finish_parser.add_argument("--repo-root", type=Path)
    finish_parser.set_defaults(func=finish)

    complete_parser = sub.add_parser("complete", help="Close a synthesized run and release the workspace recursion guard.")
    complete_parser.add_argument("run", help="Run ID, unique prefix, or run directory.")
    complete_parser.add_argument("--run-root", type=Path)
    complete_parser.add_argument("--repo-root", type=Path)
    complete_parser.set_defaults(func=complete)

    status_parser = sub.add_parser("status", help="Print active/recent jobs or one run's artifact status.")
    status_parser.add_argument("run", nargs="?", help="Optional run ID, unique prefix, or run directory.")
    status_parser.add_argument("--all", action="store_true", help="List all retained jobs instead of the newest eight.")
    status_parser.add_argument("--wait", action="store_true", help="When inspecting one run, wait while the peer is alive.")
    status_parser.add_argument("--timeout-seconds", type=float, default=2700)
    status_parser.add_argument("--poll-interval-seconds", type=float, default=2)
    status_parser.add_argument("--run-root", type=Path)
    status_parser.add_argument("--repo-root", type=Path)
    status_parser.set_defaults(func=status)

    result_parser = sub.add_parser("result", help="Print complete stored output for a run.")
    result_parser.add_argument("run", nargs="?", help="Run ID, unique prefix, or run directory. Defaults to latest run.")
    result_parser.add_argument("--run-root", type=Path)
    result_parser.add_argument("--repo-root", type=Path)
    result_parser.set_defaults(func=result)

    clear_history_parser = sub.add_parser("clear-history", help="Delete old Agent Collab run history.")
    clear_history_parser.add_argument("--retain", type=int, help="Number of newest terminal runs to keep.")
    clear_history_parser.add_argument("--all", action="store_true", help="Delete all terminal runs.")
    clear_history_parser.add_argument("--run", help="Run ID, unique prefix, or run directory to delete.")
    clear_history_parser.add_argument("--dry-run", action="store_true", help="Show deletion candidates without deleting.")
    clear_history_parser.add_argument("--yes", action="store_true", help="Confirm deletion without prompting.")
    clear_history_parser.add_argument("--run-root", type=Path)
    clear_history_parser.add_argument("--repo-root", type=Path)
    clear_history_parser.set_defaults(func=clear_history)

    cancel_parser = sub.add_parser("cancel", help="Cancel an active peer run.")
    cancel_parser.add_argument("run", help="Run ID, unique prefix, or run directory.")
    cancel_parser.add_argument(
        "--force-before-min-wait",
        action="store_true",
        help="Allow cancellation before the minimum peer wait; requires --reason.",
    )
    cancel_parser.add_argument("--reason", help="Required with --force-before-min-wait; use USER_REQUESTED_STOP for user stop requests.")
    cancel_parser.add_argument("--run-root", type=Path)
    cancel_parser.add_argument("--repo-root", type=Path)
    cancel_parser.set_defaults(func=cancel)

    doctor_parser = sub.add_parser("doctor", help="Check Agent Collab local runtime prerequisites.")
    doctor_parser.add_argument("--run-root", type=Path)
    doctor_parser.add_argument("--repo-root", type=Path)
    doctor_parser.set_defaults(func=doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
