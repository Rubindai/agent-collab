#!/usr/bin/env python3
from __future__ import annotations

from contextlib import contextmanager
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - non-Unix fallback
    fcntl = None


STATE_VERSION = 1
MAX_JOBS = 50
ACTIVE_STATUSES = {"running", "starting"}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def state_file(run_root: Path) -> Path:
    return run_root / "state.json"


def lock_file(run_root: Path) -> Path:
    return run_root / "state.lock"


def default_state() -> dict[str, Any]:
    return {"version": STATE_VERSION, "jobs": []}


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
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"warning: ignoring invalid Agent Collab state file {path}: {exc}", file=sys.stderr)
        return default_state()
    if not isinstance(parsed, dict):
        return default_state()
    jobs = parsed.get("jobs")
    return {
        "version": STATE_VERSION,
        "jobs": jobs if isinstance(jobs, list) else [],
    }


def prune_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sorted_jobs = sorted(
        [job for job in jobs if isinstance(job, dict)],
        key=lambda job: str(job.get("updated_at") or job.get("created_at") or ""),
        reverse=True,
    )
    active = [job for job in sorted_jobs if job.get("status") in ACTIVE_STATUSES]
    terminal = [job for job in sorted_jobs if job.get("status") not in ACTIVE_STATUSES]
    keep_terminal = max(MAX_JOBS - len(active), 0)
    return active + terminal[:keep_terminal]


def save_state(run_root: Path, state: dict[str, Any]) -> dict[str, Any]:
    run_root.mkdir(parents=True, exist_ok=True)
    next_state = {
        "version": STATE_VERSION,
        "jobs": prune_jobs([job for job in state.get("jobs", []) if isinstance(job, dict)]),
    }
    path = state_file(run_root)
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp_path.write_text(
            json.dumps(next_state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return next_state


def upsert_job(run_root: Path, patch: dict[str, Any]) -> dict[str, Any]:
    job_id = str(patch.get("id") or "")
    if not job_id:
        raise ValueError("job patch requires id")
    with state_lock(run_root):
        state = load_state(run_root)
        timestamp = now_iso()
        jobs = [job for job in state["jobs"] if isinstance(job, dict)]
        for index, job in enumerate(jobs):
            if job.get("id") == job_id:
                jobs[index] = {**job, **patch, "updated_at": timestamp}
                state["jobs"] = jobs
                save_state(run_root, state)
                return jobs[index]
        job = {"created_at": timestamp, "updated_at": timestamp, **patch}
        jobs.insert(0, job)
        state["jobs"] = jobs
        save_state(run_root, state)
        return job


def list_jobs(run_root: Path) -> list[dict[str, Any]]:
    return prune_jobs([job for job in load_state(run_root)["jobs"] if isinstance(job, dict)])


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
