#!/usr/bin/env python3
from __future__ import annotations

from contextlib import contextmanager
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import validation as V
import strict_json as STRICT_JSON


STATE_SCHEMA_VERSION = "2.0"
MAX_JOBS = 50
ACTIVE_STATUSES = {"running", "synthesizing"}
ORIGINS = V.ORIGINS
MODES = V.MODES
PHASES_BY_STATUS = {
    "running": {"peer_running", "normalizing_peer", "waiting_for_peer"},
    "synthesizing": {"ready_for_synthesis"},
    "completed": {"done"},
    "failed": {"done"},
    "cancelled": {"cancelled"},
}
PEER_STATUSES = {"ok", "peer_failed"}
PEER_VERDICTS = V.VERDICTS
VALIDATION_STATUSES = V.VALIDATION_STATUSES
NORMALIZATION_SOURCES = {"structured_output", "direct_json", "none"}
FINISH_TIMEOUT_SOURCES = V.FINISH_TIMEOUT_SOURCES
MIN_FINISH_WAIT_SECONDS = 60
MAX_FINISH_WAIT_SECONDS = 86_430
STATE_LOCK_TIMEOUT_SECONDS = 10.0
STATE_LOCK_POLL_SECONDS = 0.02
JOB_REQUIRED_FIELDS = {
    **dict.fromkeys(("created_at", "updated_at", "id", "run_dir", "repo_root", "target"), V.NONEMPTY),
    "host": ORIGINS, "peer": ORIGINS, "mode": MODES, "adjudicator_enabled": bool,
    "status": set(PHASES_BY_STATUS), "phase": str,
}
JOB_REQUIRED_KEYS = set(JOB_REQUIRED_FIELDS)
JOB_OPTIONAL_FIELDS = {
    **dict.fromkeys(("peer_report", "peer_raw", "peer_normalization", "host_first_pass", "peer_stderr",
                    "completed_at", "cancel_result"), V.NONEMPTY),
    "summary": str, "pid": (V.integer(1), type(None)), "edit_allowed": bool, "workspace_mutation": bool,
    "finish_wait_seconds": (V.number(MIN_FINISH_WAIT_SECONDS, MAX_FINISH_WAIT_SECONDS), type(None)),
    "finish_timeout_source": FINISH_TIMEOUT_SOURCES, "peer_status": PEER_STATUSES,
    "peer_verdict": PEER_VERDICTS, "validation_status": VALIDATION_STATUSES,
    "normalization_source": NORMALIZATION_SOURCES,
    "cancel": {"forced": bool, "reason": V.NONEMPTY, "elapsed_seconds": (V.NUMBER, type(None)),
               "minimum_wait_seconds": V.NUMBER, "before_minimum_wait": bool},
}
JOB_OPTIONAL_KEYS = set(JOB_OPTIONAL_FIELDS)
JOB_KEYS = JOB_REQUIRED_KEYS | JOB_OPTIONAL_KEYS
RUN_ID_SAFE_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def state_file(run_root: Path) -> Path:
    return run_root / "state.json"


def lock_file(run_root: Path) -> Path:
    return run_root / "state.lock"


def default_state() -> dict[str, Any]:
    return {"schema_version": STATE_SCHEMA_VERSION, "jobs": []}


def is_number(value: Any) -> bool:
    return V.NUMBER.accepts(value)


def validate_job(job: Any, label: str = "state job") -> None:
    V.record(job, JOB_REQUIRED_FIELDS, label, optional=JOB_OPTIONAL_FIELDS)
    if any(char not in RUN_ID_SAFE_CHARS for char in job["id"]):
        raise ValueError(f"{label} id contains unsafe characters")
    if job["host"] == job["peer"]:
        raise ValueError(f"{label} host and peer must be different current products")
    status = job["status"]
    if job["phase"] not in PHASES_BY_STATUS[status]:
        raise ValueError(f"{label} status/phase combination is invalid")
    if status == "cancelled":
        if "cancel" not in job or "cancel_result" not in job:
            raise ValueError(f"{label} cancelled jobs require cancel and cancel_result evidence")
    elif "cancel" in job or "cancel_result" in job:
        raise ValueError(f"{label} cancellation evidence is allowed only for cancelled jobs")


def validate_state_payload(parsed: Any) -> dict[str, Any]:
    V.record(parsed, {"schema_version": V.VERSION, "jobs": list}, "Agent Collab state")
    jobs = parsed["jobs"]
    for index, job in enumerate(jobs):
        validate_job(job, f"Agent Collab state jobs[{index}]")
    job_ids = [job["id"] for job in jobs]
    if len(job_ids) != len(set(job_ids)):
        raise ValueError("Agent Collab state contains duplicate job ids")
    return {"schema_version": STATE_SCHEMA_VERSION, "jobs": jobs}


@contextmanager
def state_lock(run_root: Path):
    STRICT_JSON.ensure_private_directory(run_root)
    path = lock_file(run_root)
    descriptor = STRICT_JSON.open_lock_file(path)
    with os.fdopen(descriptor, "a+", encoding="utf-8") as lock:
        deadline = time.monotonic() + STATE_LOCK_TIMEOUT_SECONDS
        while not STRICT_JSON.try_lock(lock.fileno()):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out acquiring Agent Collab state lock: {path}")
            time.sleep(STATE_LOCK_POLL_SECONDS)
        try:
            yield
        finally:
            # Closing still releases the lock if unlocking fails.
            try:
                STRICT_JSON.unlock(lock.fileno())
            except OSError:
                pass


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
    STRICT_JSON.ensure_private_directory(run_root)
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
