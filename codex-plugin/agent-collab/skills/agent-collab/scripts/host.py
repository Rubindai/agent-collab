#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
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
SETTINGS_SCHEMA_VERSION = "1.0"
DEFAULT_HISTORY_RETAINED_RUNS = 50
SETTING_DEFAULTS: dict[str, Any] = {
    "profile": "ultra",
    "local_subagents_allowed": True,
    "max_local_subagents": 8,
    "agent_timeout_seconds": "2700",
    "safe_mode": False,
    "codex_model": "gpt-5.5",
    "codex_effort": "xhigh",
    "web_research": "live",
    "codex_config": [],
    "claude_model": "opus",
    "claude_effort": "max",
    "claude_tools": "default",
    "claude_max_budget_usd": "",
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
    "web_research": "AGENT_COLLAB_WEB_RESEARCH",
    "codex_config": "CODEX_AGENT_COLLAB_CONFIG",
    "claude_model": "CLAUDE_AGENT_COLLAB_MODEL",
    "claude_effort": "CLAUDE_AGENT_COLLAB_EFFORT",
    "claude_tools": "CLAUDE_AGENT_COLLAB_TOOLS",
    "claude_max_budget_usd": "CLAUDE_AGENT_COLLAB_MAX_BUDGET_USD",
    "claude_max_turns": "CLAUDE_AGENT_COLLAB_MAX_TURNS",
    "history_retained_runs": "AGENT_COLLAB_HISTORY_RETAINED_RUNS",
}
PROFILE_CHOICES = {"standard", "max", "ultra"}
CODEX_EFFORT_CHOICES = {"minimal", "low", "medium", "high", "xhigh"}
CLAUDE_EFFORT_CHOICES = {"low", "medium", "high", "xhigh", "max"}
WEB_RESEARCH_CHOICES = {"cached", "live", "disabled"}
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
DEFAULT_FINISH_TIMEOUT_SECONDS = MIN_PEER_WAIT_SECONDS
FINISH_TIMEOUT_GRACE_SECONDS = 30
FINISH_WAIT_POLL_SECONDS = 1
PEER_REPORT_STABLE_SECONDS = 0.5
RESET_SCOPES = {"local", "global", "all"}
RUN_ARTIFACT_NAMES = {
    "host-request.json",
    "peer-process.json",
    "peer-report.json",
    "host-result.json",
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


def load_peer_runtime() -> Any:
    path = runtime_dir() / "peer.py"
    spec = importlib.util.spec_from_file_location("agent_collab_peer", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load peer runtime: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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
        return file_value.read_text(encoding="utf-8").strip()
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
    if raw in {"", "0"}:
        return "0"
    try:
        seconds = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout must be 0 or a positive number of seconds") from exc
    if seconds <= 0:
        return "0"
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
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
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


def normalize_settings(raw_settings: dict[str, Any], source: str) -> dict[str, Any]:
    unknown = set(raw_settings) - set(SETTING_DEFAULTS)
    if unknown:
        raise ValueError(f"{source} contains unknown setting keys: {sorted(unknown)}")
    normalized: dict[str, Any] = {}
    for key, value in raw_settings.items():
        if key in {"local_subagents_allowed", "safe_mode"}:
            normalized[key] = parse_bool(value)
        elif key == "max_local_subagents":
            number = int(value)
            if number < 0:
                raise ValueError(f"{source} max_local_subagents must be non-negative")
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
            choices = CODEX_EFFORT_CHOICES if key == "codex_effort" else CLAUDE_EFFORT_CHOICES
            if text not in choices:
                raise ValueError(f"{source} {key} must be one of {sorted(choices)}")
            normalized[key] = text
        elif key == "web_research":
            text = str(value).strip()
            if text not in WEB_RESEARCH_CHOICES:
                raise ValueError(f"{source} web_research must be one of {sorted(WEB_RESEARCH_CHOICES)}")
            normalized[key] = text
        elif key == "codex_config":
            normalized[key] = normalize_codex_config(value, source)
        elif key == "agent_timeout_seconds":
            normalized[key] = normalize_timeout(value)
        elif key == "claude_max_turns":
            text = str(value).strip()
            if not text or int(text) <= 0:
                raise ValueError(f"{source} claude_max_turns must be positive")
            normalized[key] = text
        elif key == "claude_max_budget_usd":
            text = str(value).strip()
            if text.lower() in {"none", "off", "unlimited"}:
                text = ""
            if text and float(text) <= 0:
                raise ValueError(f"{source} claude_max_budget_usd must be positive")
            normalized[key] = text
        else:
            text = str(value).strip()
            if not text:
                raise ValueError(f"{source} {key} must not be empty")
            normalized[key] = text
    return normalized


def load_settings_file(path: Path, source: str) -> tuple[dict[str, Any], str | None]:
    if not path.exists():
        return {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("settings file must contain a JSON object")
        raw_settings = data.get("settings")
        if not isinstance(raw_settings, dict):
            raise ValueError("settings file must contain a settings object")
        return normalize_settings(raw_settings, source), None
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
    for scope in ("global", "local"):
        for key, value in layers[scope]["settings"].items():
            effective[key] = value
            sources[key] = scope
    for key, env_key in SETTING_ENV_KEYS.items():
        if env_key in env:
            effective[key] = normalize_settings({key: env[env_key]}, f"environment {env_key}")[key]
            sources[key] = f"env:{env_key}"
    return {"settings": effective, "sources": sources, "layers": layers}


def settings_error_messages(resolved: dict[str, Any]) -> list[str]:
    return [
        f"{scope} settings ({layer['path']}): {layer['error']}"
        for scope, layer in resolved["layers"].items()
        if layer.get("error")
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
        "AGENT_COLLAB_WEB_RESEARCH": str(settings["web_research"]),
        "CODEX_AGENT_COLLAB_CONFIG": json.dumps(settings["codex_config"], ensure_ascii=False),
        "CLAUDE_AGENT_COLLAB_MODEL": str(settings["claude_model"]),
        "CLAUDE_AGENT_COLLAB_EFFORT": str(settings["claude_effort"]),
        "CLAUDE_AGENT_COLLAB_TOOLS": str(settings["claude_tools"]),
        "CLAUDE_AGENT_COLLAB_MAX_BUDGET_USD": "",
        "CLAUDE_AGENT_COLLAB_MAX_TURNS": str(settings["claude_max_turns"]),
        "AGENT_COLLAB_HISTORY_RETAINED_RUNS": str(settings["history_retained_runs"]),
    }


def write_settings_file(path: Path, settings: dict[str, Any]) -> None:
    normalized = normalize_settings(settings, str(path))
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


def run_snapshot(repo_root: Path, output_path: Path, ignored_paths: list[Path] | None = None) -> None:
    snapshot = load_snapshot_runtime()
    snapshot.write_workspace_snapshot(repo_root, output_path, ignored_paths=ignored_paths)


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
    try:
        return load_json(path)
    except Exception:
        return None


def stderr_tail(path: Path, limit: int = 4000) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[-limit:]


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
    process_info = read_json_if_exists(run_dir / "peer-process.json") or {}
    peer_report = read_json_if_exists(run_dir / "peer-report.json")
    normalization = read_json_if_exists(run_dir / "peer-normalization.json")
    pid = process_info.get("pid")
    peer_alive = process_alive(int(pid)) if isinstance(pid, int) else False
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
        "elapsed_seconds": json_number(elapsed_seconds),
        "minimum_wait_seconds": MIN_PEER_WAIT_SECONDS,
        "minimum_wait_remaining_seconds": (
            json_number(max(0.0, MIN_PEER_WAIT_SECONDS - elapsed_seconds))
            if elapsed_seconds is not None
            else None
        ),
        "early_cancel_blocked": early_cancel_blocked,
        "empty_output_guidance": "Empty peer-report.json or stderr does not imply a stalled peer while the peer process is alive.",
        "phase": (read_json_if_exists(run_dir / "host-result.json") or {}).get("phase"),
        "peer_status": peer_report.get("status") if peer_report else None,
        "peer_verdict": peer_report.get("verdict") if peer_report else None,
        "normalization_source": normalization.get("source") if normalization else None,
        "validation_status": normalization.get("validation_status") if normalization else None,
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
            "peer_stderr": file_info(run_dir / "peer.stderr.log"),
        },
    }


def resolve_finish_wait_timeout(
    explicit_timeout_seconds: float | None,
    process_info: dict[str, Any],
) -> tuple[float | None, str]:
    if explicit_timeout_seconds is not None:
        if explicit_timeout_seconds <= 0:
            return None, "explicit_indefinite"
        if explicit_timeout_seconds < MIN_PEER_WAIT_SECONDS:
            return float(MIN_PEER_WAIT_SECONDS), "explicit_minimum_floor"
        return explicit_timeout_seconds, "explicit"

    if "peer_timeout_seconds" in process_info:
        peer_timeout = process_info["peer_timeout_seconds"]
        if peer_timeout is None:
            return None, "peer_timeout_indefinite"
        try:
            derived = float(peer_timeout) + FINISH_TIMEOUT_GRACE_SECONDS
            if derived < MIN_PEER_WAIT_SECONDS:
                return float(MIN_PEER_WAIT_SECONDS), "peer_timeout_minimum_floor"
            return derived, "peer_timeout_plus_grace"
        except (TypeError, ValueError):
            return DEFAULT_FINISH_TIMEOUT_SECONDS, "invalid_peer_timeout_fallback"

    return DEFAULT_FINISH_TIMEOUT_SECONDS, "legacy_default"


def wait_for_peer_report(peer_report_path: Path, pid: int, timeout_seconds: float | None) -> str:
    start = time.time()
    deadline = None if timeout_seconds is None else start + timeout_seconds
    last_signature: tuple[int, int] | None = None
    stable_since: float | None = None
    while True:
        if peer_report_path.exists() and peer_report_path.stat().st_size > 0:
            stat = peer_report_path.stat()
            signature = (stat.st_size, stat.st_mtime_ns)
            alive = process_alive(pid)
            if not alive:
                return "report_ready"
            now = time.time()
            if signature != last_signature:
                last_signature = signature
                stable_since = now
            if deadline is not None and now >= deadline:
                return "timeout"
            if stable_since is not None and now - stable_since >= PEER_REPORT_STABLE_SECONDS:
                return "report_ready"
        else:
            alive = process_alive(pid)
            now = time.time()
            if deadline is not None and now >= deadline:
                return "timeout"
        if not alive:
            return "peer_exited"
        time.sleep(FINISH_WAIT_POLL_SECONDS)


def start(args: argparse.Namespace) -> int:
    repo_root = (args.repo_root or default_repo_root()).resolve()
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
    run_root = (args.run_root or default_run_root(repo_root)).resolve()
    run_dir = run_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    state = load_state_runtime()

    profile = args.profile or settings["profile"]
    max_local_subagents = args.max_local_subagents if args.max_local_subagents is not None else settings["max_local_subagents"]
    local_subagents_allowed = False if args.no_local_subagents else bool(settings["local_subagents_allowed"])
    request = {
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
        "web_research": settings["web_research"],
    }
    peer_runtime = load_peer_runtime()
    peer_runtime.validate_request(request)

    request_path = run_dir / "host-request.json"
    write_json(request_path, request)
    run_snapshot(repo_root, run_dir / "before.snapshot", ignored_paths=[run_dir])
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
            "profile": profile,
            "status": "running",
            "phase": "starting",
            "edit_allowed": bool(args.edit_allowed),
            "peer_report": str(run_dir / "peer-report.json"),
            "peer_raw": str(run_dir / "peer.raw.json"),
            "peer_normalization": str(run_dir / "peer-normalization.json"),
            "host_first_pass": str(run_dir / "host-first-pass.json"),
        },
    )

    peer_stdout = (run_dir / "peer-report.json").open("w", encoding="utf-8")
    peer_stderr = (run_dir / "peer.stderr.log").open("w", encoding="utf-8")
    env = dict(os.environ)
    for key, value in settings_to_env(settings).items():
        env[key] = value
    env["AGENT_COLLAB_PROFILE"] = profile
    env["AGENT_COLLAB_WEB_RESEARCH"] = settings["web_research"]
    env["AGENT_COLLAB_RUN_DIR"] = str(run_dir)
    agent_timeout = peer_runtime.timeout_seconds(env)
    if agent_timeout is not None:
        env["AGENT_COLLAB_TIMEOUT_SECONDS"] = (
            str(int(agent_timeout)) if float(agent_timeout).is_integer() else str(agent_timeout)
        )
    started_at_epoch = time.time()
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
        ],
        cwd=repo_root,
        stdin=subprocess.DEVNULL,
        stdout=peer_stdout,
        stderr=peer_stderr,
        env=env,
        text=True,
        start_new_session=True,
    )
    peer_stdout.close()
    peer_stderr.close()

    process_info = {
        "pid": process.pid,
        "pgid": process.pid,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "repo_root": str(repo_root),
        "host": host,
        "peer": peer,
        "profile": profile,
        "started_at": utc_timestamp(started_at_epoch),
        "started_at_epoch": started_at_epoch,
        "peer_timeout_seconds": json_number(agent_timeout),
        "settings": {
            "local": resolved["layers"]["local"]["path"],
            "global": resolved["layers"]["global"]["path"],
        },
        "peer_report": str(run_dir / "peer-report.json"),
        "peer_raw": str(run_dir / "peer.raw.json"),
        "peer_normalization": str(run_dir / "peer-normalization.json"),
        "host_first_pass": str(run_dir / "host-first-pass.json"),
    }
    write_json(run_dir / "peer-process.json", process_info)
    state.upsert_job(
        run_root,
        {
            "id": run_id,
            "status": "running",
            "phase": "peer_running",
            "pid": process.pid,
            "profile": profile,
            "peer_stderr": str(run_dir / "peer.stderr.log"),
        },
    )
    print(json.dumps(process_info, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
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
    allow_extra: bool = True,
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
    if report["schema_version"] != "1.0":
        raise ArtifactValidationError("host-first-pass schema_version must be 1.0")
    if report["run_id"] != request.get("run_id"):
        raise ArtifactValidationError("host-first-pass run_id does not match host request")
    if not isinstance(report["summary"], str):
        raise ArtifactValidationError("host-first-pass summary must be a string")
    if not isinstance(report["claims"], list):
        raise ArtifactValidationError("host-first-pass claims must be an array")
    for claim in report["claims"]:
        validate_claim_shape(claim, "host-first-pass")


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
    if report["schema_version"] != "1.0":
        raise ArtifactValidationError("claim-matrix schema_version must be 1.0")
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
    if report["schema_version"] != "1.0":
        raise ArtifactValidationError("adjudicator-report schema_version must be 1.0")
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


def load_helper_claims(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "helper-reports.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    reports: list[Any]
    if isinstance(data, dict):
        reports = []
        if isinstance(data.get("claims"), list):
            reports.append(data)
        if isinstance(data.get("reports"), list):
            reports.extend(data["reports"])
    elif isinstance(data, list):
        reports = data
    else:
        raise ArtifactValidationError("helper-reports must be an object or array")

    claims: list[dict[str, Any]] = []
    for report in reports:
        if not isinstance(report, dict):
            raise ArtifactValidationError("helper report must be an object")
        claims.extend(claims_with_source(report, "helper"))
    return claims


def build_claim_matrix(
    request: dict[str, Any],
    host_report: dict[str, Any],
    peer_report: dict[str, Any],
    helper_claims: list[dict[str, Any]] | None = None,
    adjudicator_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    claim_matrix = {
        "schema_version": "1.0",
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
        "schema_version": "1.0",
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
    raw_path = run_dir / "peer.raw.json"
    normalization_path = run_dir / "peer-normalization.json"
    sources: list[tuple[str, Path]] = [("peer_report", peer_report_path)]
    if raw_path.exists():
        sources.append(("peer_raw", raw_path))

    last_error: Exception | None = None
    for source_name, source_path in sources:
        if not source_path.exists() or source_path.stat().st_size == 0:
            continue
        try:
            normalized = peer_runtime.normalize_json_payload(source_path.read_text(encoding="utf-8").strip())
            peer_runtime.validate_peer_report(normalized.report)
            validate_peer_report_matches_request(normalized.report, request)
        except Exception as exc:
            last_error = exc
            continue
        if (
            source_name == "peer_report"
            and raw_path.exists()
            and normalized.report.get("status") == "peer_failed"
            and isinstance(normalized.report.get("error"), dict)
            and normalized.report["error"].get("kind") in {"invalid_json", "schema_validation_failed", "invalid_peer_report"}
        ):
            last_error = ValueError("peer-report.json is a parser failure wrapper; retrying peer.raw.json")
            continue

        metadata = dict(normalized.metadata)
        metadata["artifact_source"] = source_name
        metadata["validation_status"] = "ok"
        write_json(normalization_path, metadata)
        write_json(peer_report_path, normalized.report)
        return normalized.report, "ok"

    message = str(last_error) if last_error is not None else "peer report missing or empty"
    if isinstance(last_error, getattr(peer_runtime, "PeerReportValidationError")):
        failure_kind = "schema_validation_failed"
    elif isinstance(last_error, PeerReportRequestMismatch):
        failure_kind = "peer_report_mismatch"
    else:
        failure_kind = "invalid_json"
    peer_report = peer_runtime.failure(failure_kind, message, request)
    write_json(peer_report_path, peer_report)
    write_json(
        normalization_path,
        {
            "schema_version": "1.0",
            "source": "none",
            "artifact_source": "peer_raw" if raw_path.exists() else "peer_report",
            "input_bytes": raw_path.stat().st_size if raw_path.exists() else peer_report_path.stat().st_size if peer_report_path.exists() else 0,
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
    request = load_json(run_dir / "host-request.json")
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
    pid = int(process_info["pid"])
    peer_report_path = run_dir / "peer-report.json"
    finish_wait_seconds, finish_timeout_source = resolve_finish_wait_timeout(args.timeout_seconds, process_info)
    wait_status = wait_for_peer_report(peer_report_path, pid, finish_wait_seconds)

    if wait_status == "timeout" and process_alive(pid):
        output = {
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
        write_json(run_dir / "host-result.json", output)
        state.upsert_job(
            run_root,
            {
                **job_patch_from_run(run_dir, "running", "waiting_for_peer"),
                "pid": pid,
                "finish_wait_seconds": json_number(finish_wait_seconds),
                "finish_timeout_source": finish_timeout_source,
            },
        )
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
        return 1

    if not peer_report_path.exists() or peer_report_path.stat().st_size == 0:
        failure = {
            "schema_version": "1.0",
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
            "error": {"kind": "peer_no_output", "message": "Peer report missing or empty."},
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
        helper_claims = load_helper_claims(run_dir)
        claim_matrix = build_claim_matrix(request, host_report, peer_report, helper_claims, adjudicator)
    except ArtifactValidationError as exc:
        raise SystemExit(f"invalid helper or claim-matrix artifact in {run_dir}: {exc}") from exc
    write_json(run_dir / "claim-matrix.json", claim_matrix)

    repo_root = Path(process_info.get("repo_root", str(default_repo_root()))).resolve()
    run_snapshot(repo_root, run_dir / "after.snapshot", ignored_paths=[run_dir])
    result = {
        "run_id": request["run_id"],
        "run_dir": str(run_dir),
        "peer_status": peer_report.get("status"),
        "validation_status": validation_status,
        "finish_wait_seconds": json_number(finish_wait_seconds),
        "finish_timeout_source": finish_timeout_source,
        "normalization": str(run_dir / "peer-normalization.json"),
        "claim_matrix": str(run_dir / "claim-matrix.json"),
        "adjudicator_report": str(run_dir / "adjudicator-report.json"),
    }
    write_json(run_dir / "host-result.json", {**result, "phase": "done"})
    state_status = "completed" if peer_report.get("status") == "ok" else "failed"
    state.upsert_job(
        run_root,
        {
            **job_patch_from_run(run_dir, state_status, "done"),
            "peer_status": peer_report.get("status"),
            "peer_verdict": peer_report.get("verdict"),
            "validation_status": validation_status,
            "normalization_source": (read_json_if_exists(run_dir / "peer-normalization.json") or {}).get("source"),
            "completed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "summary": peer_report.get("summary"),
        },
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
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
        "peer_normalization": read_json_if_exists(run_dir / "peer-normalization.json"),
        "peer_report": read_json_if_exists(run_dir / "peer-report.json"),
        "claim_matrix": read_json_if_exists(run_dir / "claim-matrix.json"),
        "adjudicator_report": read_json_if_exists(run_dir / "adjudicator-report.json"),
        "host_result": read_json_if_exists(run_dir / "host-result.json"),
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
    active_statuses = getattr(state_runtime, "ACTIVE_STATUSES", {"running", "starting"})
    candidates = history_candidates(run_root, state_runtime)

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

    active_preserved = [candidate for candidate in selected if candidate["status"] in active_statuses]
    all_active_preserved = [candidate for candidate in candidates if candidate["status"] in active_statuses]
    deletable = [candidate for candidate in selected if candidate["status"] not in active_statuses]
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
    settings = resolve_settings(repo_root)["settings"]
    status, output = build_clear_history_output(args, int(settings.get("history_retained_runs", DEFAULT_HISTORY_RETAINED_RUNS)))
    if output is not None:
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return status


def terminate_process_group(pid: int) -> str:
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return "not_running"
    except PermissionError:
        os.kill(pid, signal.SIGTERM)
    deadline = time.time() + 5
    while time.time() < deadline:
        if not process_alive(pid):
            return "terminated"
        time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return "terminated"
    except PermissionError:
        os.kill(pid, signal.SIGKILL)
    return "killed"


def process_identity_matches(process_info: dict[str, Any]) -> bool:
    expected_pgid = process_info.get("pgid")
    if expected_pgid is None:
        return True
    try:
        return os.getpgid(int(process_info["pid"])) == int(expected_pgid)
    except (ProcessLookupError, PermissionError, KeyError, TypeError, ValueError):
        return False


def cancel(args: argparse.Namespace) -> int:
    repo_root = (args.repo_root or default_repo_root()).resolve()
    run_root = (args.run_root or default_run_root(repo_root)).resolve()
    run_dir = resolve_run_reference(repo_root, args.run, run_root)
    process_info = load_json(run_dir / "peer-process.json")
    request = load_json(run_dir / "host-request.json")
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

    outcome = terminate_process_group(pid) if identity_matches else "pid_mismatch"
    cancel_details = {
        "forced": force_before_min_wait,
        "reason": reason or ("minimum_wait_elapsed" if not before_minimum_wait else "unspecified"),
        "elapsed_seconds": json_number(elapsed_seconds),
        "minimum_wait_seconds": MIN_PEER_WAIT_SECONDS,
        "before_minimum_wait": before_minimum_wait,
    }
    peer_report_path = run_dir / "peer-report.json"
    if not peer_report_path.exists() or peer_report_path.stat().st_size == 0:
        peer_runtime = load_peer_runtime()
        write_json(
            peer_report_path,
            peer_runtime.failure("cancelled", "Peer run was cancelled by host.", request, cancel_details),
        )
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
    output = {
        "run_id": request["run_id"],
        "run_dir": str(run_dir),
        "pid": pid,
        "outcome": outcome,
        "forced": force_before_min_wait,
        "reason": reason or cancel_details["reason"],
        "elapsed_seconds": json_number(elapsed_seconds),
        "minimum_wait_seconds": MIN_PEER_WAIT_SECONDS,
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
        "web_research": "web_research",
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
    if getattr(args, "wait_until_response", False):
        updates["agent_timeout_seconds"] = "0"
    elif getattr(args, "timeout_seconds", None) is not None:
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
        "0": "wait until response",
        "2700": "45 minutes",
        "3600": "60 minutes",
    }
    while True:
        current_label = labels.get(current, f"{current} seconds")
        print(f"Peer timeout [{current_label}]")
        print("  1. wait until response")
        print("  2. 45 minutes")
        print("  3. 60 minutes")
        print("  4. custom seconds")
        answer = input("> ").strip()
        if not answer:
            return current
        if answer == "1":
            return "0"
        if answer == "2":
            return "2700"
        if answer == "3":
            return "3600"
        if answer == "4":
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
    settings["codex_effort"] = choose_option("Codex reasoning effort", str(settings["codex_effort"]), ["xhigh", "high", "medium", "low", "minimal"])
    settings["web_research"] = choose_option("Web research capability", str(settings["web_research"]), ["live", "cached", "disabled"])
    settings["codex_config"] = choose_codex_config(list(settings["codex_config"]))
    settings["claude_model"] = choose_text("Claude peer model", str(settings["claude_model"]))
    settings["claude_effort"] = choose_option("Claude effort", str(settings["claude_effort"]), ["max", "xhigh", "high", "medium", "low"])
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
            data = json.loads(result["stdout"])
        except json.JSONDecodeError as exc:
            error = str(exc)
    return {**result, "json": data, "json_error": error}


def command_help(command: list[str]) -> str:
    result = run_command_capture(command, timeout=5)
    return f"{result.get('stdout', '')}\n{result.get('stderr', '')}"


def summarize_codex_doctor(report: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {}
    checks = report.get("checks") if isinstance(report.get("checks"), dict) else {}
    noteworthy = [
        {
            "id": check.get("id"),
            "status": check.get("status"),
            "summary": check.get("summary"),
            "remediation": check.get("remediation"),
        }
        for check in checks.values()
        if isinstance(check, dict) and check.get("status") in {"warning", "fail"}
    ]
    return {
        "overall_status": report.get("overallStatus"),
        "codex_version": report.get("codexVersion"),
        "noteworthy": noteworthy,
    }


def summarize_claude_auth(report: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {}
    return {
        "logged_in": report.get("loggedIn"),
        "auth_method": report.get("authMethod"),
        "api_provider": report.get("apiProvider"),
        "subscription_type": report.get("subscriptionType"),
    }


def support_map(help_text: str, options: list[str]) -> dict[str, bool]:
    return {option: option in help_text for option in options}


def doctor(args: argparse.Namespace) -> int:
    repo_root = (args.repo_root or default_repo_root()).resolve()
    run_root = (args.run_root or default_run_root(repo_root)).resolve()
    peer_runtime = load_peer_runtime()
    resolved = resolve_settings(repo_root)
    settings = resolved["settings"]
    codex_path = shutil.which("codex")
    claude_path = shutil.which("claude")
    codex_exec_help = command_help(["codex", "exec", "--help"]) if codex_path else ""
    claude_help = command_help(["claude", "--help"]) if claude_path else ""
    codex_doctor = parse_json_command(["codex", "doctor", "--json"], timeout=30) if codex_path else {}
    claude_auth = parse_json_command(["claude", "auth", "status", "--json"], timeout=10) if claude_path else {}
    codex_report = codex_doctor.get("json") if isinstance(codex_doctor, dict) else None
    claude_report = claude_auth.get("json") if isinstance(claude_auth, dict) else None
    codex_flags = support_map(
        codex_exec_help,
        [
            "--sandbox",
            "--output-schema",
            "--output-last-message",
            "--model",
            "-c",
            "--config",
            "--dangerously-bypass-approvals-and-sandbox",
            "--ask-for-approval",
        ],
    )
    claude_flag_options = [
        "--model",
        "--effort",
        "--json-schema",
        "--output-format",
        "--no-session-persistence",
        "--permission-mode",
        "--dangerously-skip-permissions",
        "--allowedTools",
        "--allowed-tools",
        "--tools",
        "--disallowedTools",
        "--disallowed-tools",
        "--max-turns",
    ]
    claude_help_flags = support_map(claude_help, claude_flag_options)
    claude_documented_flags = {
        option: option in getattr(peer_runtime, "CLAUDE_DOCUMENTED_FLAGS", set())
        for option in claude_flag_options
    }
    claude_flags = {
        option: claude_help_flags[option] or claude_documented_flags[option]
        for option in claude_flag_options
    }
    settings_errors = [
        error
        for error in (
            resolved["layers"]["global"]["error"],
            resolved["layers"]["local"]["error"],
        )
        if error
    ]
    web_research = str(settings["web_research"])
    codex_config_supported = codex_flags["-c"] or codex_flags["--config"]
    claude_tools = str(settings["claude_tools"]).strip()
    claude_custom_tools = bool(claude_tools and claude_tools != "default")
    claude_tools_supported = claude_flags["--tools"]
    claude_can_disallow_tools = claude_flags["--disallowedTools"] or claude_flags["--disallowed-tools"]
    if web_research == "disabled":
        claude_web_tools_ok = claude_can_disallow_tools or (claude_custom_tools and claude_tools_supported)
    elif claude_custom_tools:
        claude_web_tools_ok = claude_tools_supported
    else:
        claude_web_tools_ok = True
    checks = {
        "repo_root": {"ok": repo_root.exists(), "value": str(repo_root)},
        "run_root": {"ok": run_root.exists() or path_is_creatable(run_root), "value": str(run_root)},
        "python": {"ok": sys.version_info >= (3, 10), "value": sys.version.split()[0]},
        "git": {"ok": shutil.which("git") is not None, "value": shutil.which("git")},
        "claude": {"ok": claude_path is not None, "value": claude_path},
        "codex": {"ok": codex_path is not None, "value": codex_path},
        "settings": {
            "ok": not settings_errors,
            "value": {"local": resolved["layers"]["local"]["path"], "global": resolved["layers"]["global"]["path"]},
            "errors": settings_errors,
        },
        "peer_schema": {"ok": peer_runtime.default_schema_path().exists(), "value": str(peer_runtime.default_schema_path())},
        "timeout_floor_seconds": {"ok": peer_runtime.MIN_AGENT_TIMEOUT_SECONDS >= 2700, "value": peer_runtime.MIN_AGENT_TIMEOUT_SECONDS},
        "state_file": {"ok": True, "value": str(run_root / "state.json")},
        "effective_web_research": {"ok": web_research in WEB_RESEARCH_CHOICES, "value": web_research},
        "codex_doctor": {
            "ok": bool(codex_path) and isinstance(codex_report, dict) and codex_report.get("overallStatus") != "fail",
            "value": summarize_codex_doctor(codex_report),
            "returncode": codex_doctor.get("returncode") if isinstance(codex_doctor, dict) else None,
        },
        "claude_auth": {
            "ok": bool(claude_path) and isinstance(claude_report, dict) and claude_report.get("loggedIn") is True,
            "value": summarize_claude_auth(claude_report),
            "returncode": claude_auth.get("returncode") if isinstance(claude_auth, dict) else None,
        },
        "codex_flags": {
            "ok": all(codex_flags[option] for option in ["--sandbox", "--output-schema", "--output-last-message", "--model"])
            and codex_config_supported
            and (codex_flags["--dangerously-bypass-approvals-and-sandbox"] or codex_flags["--ask-for-approval"]),
            "value": codex_flags,
        },
        "claude_flags": {
            "ok": all(claude_flags[option] for option in ["--model", "--effort", "--json-schema", "--output-format", "--no-session-persistence"])
            and (claude_flags["--permission-mode"] or claude_flags["--dangerously-skip-permissions"]),
            "value": {
                "effective": claude_flags,
                "documented": claude_documented_flags,
                "help_visible": claude_help_flags,
                "note": "Claude Code docs say claude --help does not list every flag; runtime uses documented flags as effective.",
            },
        },
        "codex_web_config": {
            "ok": bool(codex_path) and codex_config_supported,
            "value": {
                "web_search": web_research,
                "config_flag_supported": codex_config_supported,
            },
        },
        "claude_web_tools": {
            "ok": bool(claude_path) and claude_web_tools_ok,
            "value": {
                "web_research": web_research,
                "custom_tools": claude_custom_tools,
                "tools_flag_supported": claude_tools_supported,
                "disallow_flag_supported": claude_can_disallow_tools,
                "web_tools": ["WebSearch", "WebFetch"],
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
    return 0


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
    start_parser.add_argument("--max-local-subagents", type=int)
    start_parser.add_argument("--no-local-subagents", action="store_true")
    start_parser.add_argument("--run-id")
    start_parser.add_argument("--run-root", type=Path)
    start_parser.add_argument("--repo-root", type=Path)
    start_parser.set_defaults(func=start)

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
    setup_parser.add_argument("--wait-until-response", action="store_true")
    setup_parser.add_argument("--history-retained-runs", type=int)
    setup_parser.add_argument("--clear-history", action="store_true", help="Clear old run history after setup.")
    setup_parser.add_argument("--yes", action="store_true", help="Confirm setup actions that delete run history.")
    setup_parser.add_argument("--safe-mode", action=argparse.BooleanOptionalAction)
    setup_parser.add_argument("--codex-model")
    setup_parser.add_argument("--codex-effort", choices=sorted(CODEX_EFFORT_CHOICES))
    setup_parser.add_argument("--web-research", choices=sorted(WEB_RESEARCH_CHOICES))
    setup_parser.add_argument(
        "--codex-config",
        action="append",
        metavar="key=value",
        help="Additional Codex config override to pass as `codex exec -c key=value`; repeat for multiple entries.",
    )
    setup_parser.add_argument("--claude-model")
    setup_parser.add_argument("--claude-effort", choices=sorted(CLAUDE_EFFORT_CHOICES))
    setup_parser.add_argument("--claude-tools")
    setup_parser.add_argument("--claude-max-budget-usd", help=argparse.SUPPRESS)
    setup_parser.add_argument("--claude-max-turns")
    setup_parser.add_argument("--repo-root", type=Path)
    setup_parser.set_defaults(func=setup)

    finish_parser = sub.add_parser("finish", help="Validate peer output and write synthesis support artifacts.")
    finish_parser.add_argument("run", help="Run ID, unique prefix, or run directory.")
    finish_parser.add_argument("--timeout-seconds", type=float, default=None)
    finish_parser.add_argument("--run-root", type=Path)
    finish_parser.add_argument("--repo-root", type=Path)
    finish_parser.set_defaults(func=finish)

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
