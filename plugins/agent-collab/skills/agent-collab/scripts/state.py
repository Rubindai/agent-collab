#!/usr/bin/env python3
from __future__ import annotations

from contextlib import contextmanager
import importlib.util
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - non-Unix fallback
    fcntl = None


STATE_SCHEMA_VERSION = "2.0"
MAX_JOBS = 50
ACTIVE_STATUSES = {"running", "synthesizing"}
ORIGINS = {"claude", "codex"}
MODES = {"review", "research", "design", "plan", "debug"}
PROFILES = {"standard", "max", "ultra"}
PHASES_BY_STATUS = {
    "running": {"peer_running", "normalizing_peer", "waiting_for_peer"},
    "synthesizing": {"ready_for_synthesis"},
    "completed": {"done"},
    "failed": {"done"},
    "cancelled": {"cancelled"},
}
PEER_STATUSES = {"ok", "peer_failed"}
PEER_VERDICTS = {
    "pass",
    "pass_with_concerns",
    "changes_recommended",
    "ready",
    "needs_revision",
    "blocked",
    "informational",
}
VALIDATION_STATUSES = {
    "ok",
    "invalid_json",
    "noncanonical_output",
    "schema_validation_failed",
    "peer_report_mismatch",
}
NORMALIZATION_SOURCES = {"structured_output", "direct_json", "none"}
FINISH_TIMEOUT_SOURCES = {
    "explicit",
    "peer_timeout_plus_grace",
}
MIN_FINISH_WAIT_SECONDS = 2_700
MAX_FINISH_WAIT_SECONDS = 86_430
JOB_REQUIRED_KEYS = {
    "created_at",
    "updated_at",
    "id",
    "run_dir",
    "repo_root",
    "host",
    "peer",
    "mode",
    "target",
    "profile",
    "status",
    "phase",
}
JOB_OPTIONAL_KEYS = {
    "pid",
    "edit_allowed",
    "peer_report",
    "peer_raw",
    "peer_normalization",
    "host_first_pass",
    "peer_stderr",
    "finish_wait_seconds",
    "finish_timeout_source",
    "peer_status",
    "peer_verdict",
    "validation_status",
    "normalization_source",
    "workspace_mutation",
    "summary",
    "completed_at",
    "cancel",
}
JOB_KEYS = JOB_REQUIRED_KEYS | JOB_OPTIONAL_KEYS
RUN_ID_SAFE_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def load_strict_json_runtime() -> Any:
    path = Path(__file__).resolve().parent / "strict_json.py"
    spec = importlib.util.spec_from_file_location("agent_collab_strict_json_state", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load strict JSON runtime: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


STRICT_JSON = load_strict_json_runtime()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def state_file(run_root: Path) -> Path:
    return run_root / "state.json"


def lock_file(run_root: Path) -> Path:
    return run_root / "state.lock"


def default_state() -> dict[str, Any]:
    return {"schema_version": STATE_SCHEMA_VERSION, "jobs": []}


def is_number(value: Any) -> bool:
    return type(value) in {int, float} and math.isfinite(value)


def validate_job(job: Any, label: str = "state job") -> None:
    if not isinstance(job, dict):
        raise ValueError(f"{label} must be an object")
    missing = JOB_REQUIRED_KEYS - job.keys()
    unknown = set(job) - JOB_KEYS
    if missing or unknown:
        raise ValueError(f"{label} must match v2 exactly; missing={sorted(missing)} unknown={sorted(unknown)}")

    for key in ("created_at", "updated_at", "id", "run_dir", "repo_root", "target"):
        if not isinstance(job[key], str) or not job[key]:
            raise ValueError(f"{label} {key} must be a non-empty string")
    if any(char not in RUN_ID_SAFE_CHARS for char in job["id"]):
        raise ValueError(f"{label} id contains unsafe characters")
    if job["host"] not in ORIGINS or job["peer"] not in ORIGINS or job["host"] == job["peer"]:
        raise ValueError(f"{label} host and peer must be different current products")
    if job["mode"] not in MODES:
        raise ValueError(f"{label} mode is invalid")
    if job["profile"] not in PROFILES:
        raise ValueError(f"{label} profile is invalid")
    status = job["status"]
    if status not in PHASES_BY_STATUS or job["phase"] not in PHASES_BY_STATUS[status]:
        raise ValueError(f"{label} status/phase combination is invalid")

    for key in ("peer_report", "peer_raw", "peer_normalization", "host_first_pass", "peer_stderr", "completed_at"):
        if key in job and (not isinstance(job[key], str) or not job[key]):
            raise ValueError(f"{label} {key} must be a non-empty string")
    if "summary" in job and not isinstance(job["summary"], str):
        raise ValueError(f"{label} summary must be a string")
    if "pid" in job and job["pid"] is not None and (type(job["pid"]) is not int or job["pid"] <= 0):
        raise ValueError(f"{label} pid must be a positive JSON integer or null")
    if "edit_allowed" in job and type(job["edit_allowed"]) is not bool:
        raise ValueError(f"{label} edit_allowed must be a JSON boolean")
    if "workspace_mutation" in job and type(job["workspace_mutation"]) is not bool:
        raise ValueError(f"{label} workspace_mutation must be a JSON boolean")
    if "finish_wait_seconds" in job:
        wait = job["finish_wait_seconds"]
        if wait is not None and (
            not is_number(wait)
            or not MIN_FINISH_WAIT_SECONDS <= float(wait) <= MAX_FINISH_WAIT_SECONDS
        ):
            raise ValueError(f"{label} finish_wait_seconds must be a finite bounded number or null")
    if "finish_timeout_source" in job and job["finish_timeout_source"] not in FINISH_TIMEOUT_SOURCES:
        raise ValueError(f"{label} finish_timeout_source is invalid")
    if "peer_status" in job and job["peer_status"] not in PEER_STATUSES:
        raise ValueError(f"{label} peer_status is invalid")
    if "peer_verdict" in job and job["peer_verdict"] not in PEER_VERDICTS:
        raise ValueError(f"{label} peer_verdict is invalid")
    if "validation_status" in job and job["validation_status"] not in VALIDATION_STATUSES:
        raise ValueError(f"{label} validation_status is invalid")
    if "normalization_source" in job and job["normalization_source"] not in NORMALIZATION_SOURCES:
        raise ValueError(f"{label} normalization_source is invalid")
    if "cancel" in job:
        cancel = job["cancel"]
        cancel_keys = {"forced", "reason", "elapsed_seconds", "minimum_wait_seconds", "before_minimum_wait"}
        if not isinstance(cancel, dict) or set(cancel) != cancel_keys:
            raise ValueError(f"{label} cancel must match v2 exactly")
        if type(cancel["forced"]) is not bool or type(cancel["before_minimum_wait"]) is not bool:
            raise ValueError(f"{label} cancel booleans must be JSON booleans")
        if not isinstance(cancel["reason"], str) or not cancel["reason"]:
            raise ValueError(f"{label} cancel reason must be a non-empty string")
        if cancel["elapsed_seconds"] is not None and not is_number(cancel["elapsed_seconds"]):
            raise ValueError(f"{label} cancel elapsed_seconds must be a number or null")
        if not is_number(cancel["minimum_wait_seconds"]):
            raise ValueError(f"{label} cancel minimum_wait_seconds must be a number")


def validate_state_payload(parsed: Any) -> dict[str, Any]:
    if not isinstance(parsed, dict) or set(parsed) != {"schema_version", "jobs"}:
        raise ValueError("Agent Collab state must contain exactly schema_version and jobs")
    if parsed["schema_version"] != STATE_SCHEMA_VERSION:
        raise ValueError(
            f"Agent Collab state schema_version must be {STATE_SCHEMA_VERSION}; legacy state is unsupported"
        )
    jobs = parsed["jobs"]
    if not isinstance(jobs, list):
        raise ValueError("Agent Collab state jobs must be an array")
    for index, job in enumerate(jobs):
        validate_job(job, f"Agent Collab state jobs[{index}]")
    job_ids = [job["id"] for job in jobs]
    if len(job_ids) != len(set(job_ids)):
        raise ValueError("Agent Collab state contains duplicate job ids")
    return {"schema_version": STATE_SCHEMA_VERSION, "jobs": jobs}


@contextmanager
def state_lock(run_root: Path):
    run_root.mkdir(parents=True, exist_ok=True)
    with lock_file(run_root).open("a+", encoding="utf-8") as lock:
        if fcntl is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def load_state(run_root: Path) -> dict[str, Any]:
    path = state_file(run_root)
    if not path.exists():
        return default_state()
    try:
        parsed = STRICT_JSON.load(path, max_bytes=16 * 1024 * 1024)
    except ValueError as exc:
        raise ValueError(f"invalid Agent Collab state JSON at {path}: {exc}") from exc
    return validate_state_payload(parsed)


def prune_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sorted_jobs = sorted(
        jobs,
        key=lambda job: str(job.get("updated_at") or job.get("created_at") or ""),
        reverse=True,
    )
    active = [job for job in sorted_jobs if job.get("status") in ACTIVE_STATUSES]
    terminal = [job for job in sorted_jobs if job.get("status") not in ACTIVE_STATUSES]
    keep_terminal = max(MAX_JOBS - len(active), 0)
    return active + terminal[:keep_terminal]


def save_state(run_root: Path, state: dict[str, Any]) -> dict[str, Any]:
    run_root.mkdir(parents=True, exist_ok=True)
    validated = validate_state_payload(state)
    next_state = {
        "schema_version": STATE_SCHEMA_VERSION,
        "jobs": prune_jobs(validated["jobs"]),
    }
    path = state_file(run_root)
    STRICT_JSON.write(path, next_state)
    return next_state


def upsert_job(run_root: Path, patch: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(patch, dict):
        raise ValueError("job patch must be an object")
    if "created_at" in patch or "updated_at" in patch:
        raise ValueError("job patch may not set state-managed timestamps")
    job_id = str(patch.get("id") or "")
    if not job_id:
        raise ValueError("job patch requires id")
    with state_lock(run_root):
        state = load_state(run_root)
        timestamp = now_iso()
        jobs = list(state["jobs"])
        for index, job in enumerate(jobs):
            if job["id"] == job_id:
                jobs[index] = {**job, **patch, "updated_at": timestamp}
                validate_job(jobs[index])
                state["jobs"] = jobs
                save_state(run_root, state)
                return jobs[index]
        job = {"created_at": timestamp, "updated_at": timestamp, **patch}
        validate_job(job)
        jobs.insert(0, job)
        state["jobs"] = jobs
        save_state(run_root, state)
        return job


def remove_job(run_root: Path, job_id: str) -> bool:
    """Remove one exact job during an atomic prelaunch rollback."""

    if not isinstance(job_id, str) or not job_id:
        raise ValueError("job removal requires a non-empty id")
    with state_lock(run_root):
        state = load_state(run_root)
        retained = [job for job in state["jobs"] if job["id"] != job_id]
        if len(retained) == len(state["jobs"]):
            return False
        state["jobs"] = retained
        save_state(run_root, state)
        return True


def list_jobs(run_root: Path) -> list[dict[str, Any]]:
    return prune_jobs(load_state(run_root)["jobs"])


def find_job(run_root: Path, reference: str | None) -> dict[str, Any] | None:
    jobs = list_jobs(run_root)
    if not jobs:
        return None
    if not reference:
        return jobs[0]
    exact = [job for job in jobs if job.get("id") == reference]
    if exact:
        return exact[0]
    prefix_matches = [job for job in jobs if str(job.get("id", "")).startswith(reference)]
    if len(prefix_matches) == 1:
        return prefix_matches[0]
    if len(prefix_matches) > 1:
        raise ValueError(f'job reference "{reference}" is ambiguous')
    return None
