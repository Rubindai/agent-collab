#!/usr/bin/env python3
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


sys.dont_write_bytecode = True
import host_config as CONFIG
import artifacts as ARTIFACTS
import registry as REGISTRY


FINISH_WAIT_POLL_SECONDS = 1


RUN_ARTIFACT_NAMES = {
    "host-request.json",
    "peer-process.json",
    "provider-process.json",
    "peer-report.json",
    "host-result.json",
    "host-synthesis.json",
    "workspace-mutation.json",
    "cancel-result.json",
    "cancel-workspace-mutation.json",
}


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    """Match whole tokens or phrases without triggering inside unrelated words."""

    return any(
        re.search(rf"(?<![\w]){re.escape(needle.strip())}(?![\w])", text) is not None
        for needle in needles
    )


def classify_mode(target: str, brief: str) -> str:
    text = f"{target}\n{brief}".casefold()
    review_context = _contains_any(
        text,
        (
            "review",
            "audit",
            "diff",
            "patch",
            "pull request",
            "pr",
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
            "hung",
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
            "official documentation",
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


def write_json(path: Path, data: dict[str, Any]) -> None:
    CONFIG.STRICT_JSON.write(path, data)


def write_json_once(path: Path, data: dict[str, Any]) -> bool:
    """Atomically publish an immutable JSON artifact without replacing evidence."""

    CONFIG.STRICT_JSON.ensure_private_directory(path.parent)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{os.urandom(6).hex()}.tmp")
    payload = (CONFIG.STRICT_JSON.dumps(data) + "\n").encode("utf-8")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | CONFIG.STRICT_JSON.BINARY_FLAG, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            return False
        if os.name == "posix":
            os.chmod(path, 0o600)
        CONFIG.STRICT_JSON.fsync_directory(path.parent)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def run_snapshot(
    repo_root: Path,
    output_path: Path,
    ignored_paths: list[Path] | None = None,
    *,
    workspace_mode: str = "direct",
) -> dict[str, Any]:
    snapshot = CONFIG.load_snapshot_runtime()
    deadline = time.monotonic() + snapshot.DEFAULT_SNAPSHOT_TIMEOUT_SECONDS
    data = (
        {"mode": "isolated", "digest": hashlib.sha256(str(repo_root).encode()).hexdigest()}
        if workspace_mode == "isolated"
        else snapshot.mutation_snapshot(repo_root, ignored_paths=ignored_paths, deadline=deadline)
    )
    write_json(ARTIFACTS.snapshot_json_path(output_path), data)
    if output_path.name == "before.snapshot":
        # Every terminal path must be able to read this baseline. Reject an
        # oversized snapshot while start can still roll back without a peer.
        ARTIFACTS.load_json(ARTIFACTS.snapshot_json_path(output_path))
    snapshot.write_workspace_snapshot(
        repo_root,
        output_path,
        ignored_paths=ignored_paths,
        deadline=deadline,
        snapshot_data=data,
    )
    return data


def record_workspace_mutation(
    repo_root: Path,
    run_dir: Path,
    request: dict[str, Any],
    *,
    after_snapshot_name: str,
    mutation_name: str,
    source: str,
) -> dict[str, Any] | None:
    before_path = ARTIFACTS.snapshot_json_path(run_dir / "before.snapshot")
    if not before_path.exists() or before_path.stat().st_size == 0:
        raise ARTIFACTS.ArtifactValidationError("canonical before.snapshot.json is missing or empty")
    before = ARTIFACTS.load_json(before_path)
    mutation_path = run_dir / mutation_name
    if source == "cancel_snapshot" and mutation_path.exists():
        existing = ARTIFACTS.load_json(mutation_path)
        ARTIFACTS.validate_cancel_mutation_evidence(existing, request, run_dir)
        return existing
    after = run_snapshot(repo_root, run_dir / after_snapshot_name, ignored_paths=[run_dir], workspace_mode=request["workspace_mode"])
    snapshot = CONFIG.load_snapshot_runtime()
    diff = snapshot.diff_snapshots(before, after)
    if diff["changed"]:
        diagnostic = {
            **diff,
            "schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION,
            "source": source,
            "edit_allowed": bool(request["edit_allowed"]),
            "message": (
                "Workspace changed during the Agent Collab run; attribution is unknown."
            ),
            "coverage_limitations": ARTIFACTS.COVERAGE_LIMITATIONS,
        }
        if source == "cancel_snapshot":
            diagnostic["before_snapshot_sha256"] = ARTIFACTS.file_sha256_or_none(before_path)
            diagnostic["after_snapshot_sha256"] = ARTIFACTS.file_sha256_or_none(
                ARTIFACTS.snapshot_json_path(run_dir / after_snapshot_name)
            )
            ARTIFACTS.validate_cancel_mutation_evidence(diagnostic, request, run_dir)
            if not write_json_once(mutation_path, diagnostic):
                existing = ARTIFACTS.load_json(mutation_path)
                if existing != diagnostic:
                    raise ARTIFACTS.ArtifactValidationError(
                        "immutable cancellation mutation evidence already exists with different content"
                    )
                return existing
        else:
            write_json(mutation_path, diagnostic)
        return diagnostic
    return ARTIFACTS.read_json_if_exists(mutation_path)


def record_host_workspace_mutation(
    repo_root: Path,
    run_dir: Path,
    request: dict[str, Any],
) -> dict[str, Any] | None:
    return record_workspace_mutation(
        repo_root,
        run_dir,
        request,
        after_snapshot_name="after.snapshot",
        mutation_name="workspace-mutation.json",
        source="host_snapshot",
    )


def record_cancel_workspace_mutation(
    repo_root: Path,
    run_dir: Path,
    request: dict[str, Any],
) -> dict[str, Any] | None:
    return record_workspace_mutation(
        repo_root,
        run_dir,
        request,
        after_snapshot_name="cancel-after.snapshot",
        mutation_name="cancel-workspace-mutation.json",
        source="cancel_snapshot",
    )


def process_alive(pid: int) -> bool:
    return CONFIG.SAFETY.pid_alive(pid)


def peer_started_at_epoch(process_info: dict[str, Any], run_dir: Path) -> float | None:
    raw_epoch = process_info.get("started_at_epoch")
    if raw_epoch is not None:
        try:
            return float(raw_epoch)
        except (TypeError, ValueError):
            pass

    parsed = ARTIFACTS.parse_utc_timestamp(process_info.get("started_at"))
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


def require_process_tree_quiescent(
    process_info: dict[str, Any],
    run_dir: Path,
    request: dict[str, Any],
    *,
    wrapper_quiescence_proven: bool = False,
) -> dict[str, Any]:
    provider = ARTIFACTS.load_provider_process(run_dir, request)
    isolation_path = run_dir / "isolation-state.json"
    if not wrapper_quiescence_proven:
        wrapper_state = CONFIG.SAFETY.process_identity_state(process_info.get("process_identity"))
        if wrapper_state == "unknown":
            # An empty-group answer cannot prove quiescence for a root whose
            # identity is unreadable.
            raise ARTIFACTS.ArtifactValidationError("peer wrapper liveness cannot be verified")
        if wrapper_state == "matching" and process_alive(int(process_info["pid"])):
            raise ARTIFACTS.ArtifactValidationError("peer wrapper is still running")
        if request["workspace_mode"] == "isolated" and provider["status"] in {"pending", "quiescent"}:
            if provider["status"] != "pending" and not isolation_path.exists():
                raise ARTIFACTS.ArtifactValidationError("isolated provider is missing its cleanup state")
            try:
                CONFIG.load_isolation_runtime().cleanup_state(isolation_path)
            except (OSError, ValueError, RuntimeError) as exc:
                raise ARTIFACTS.ArtifactValidationError(f"isolated workspace cleanup failed: {exc}") from exc
        if CONFIG.SAFETY.process_group_alive(int(process_info["pgid"])):
            raise ARTIFACTS.ArtifactValidationError("peer wrapper group still has live members")
    if request["workspace_mode"] == "isolated":
        if provider["status"] != "pending" and not isolation_path.exists():
            raise ARTIFACTS.ArtifactValidationError("isolated provider is missing its cleanup state")
        try:
            CONFIG.load_isolation_runtime().cleanup_state(isolation_path)
        except (OSError, ValueError, RuntimeError) as exc:
            raise ARTIFACTS.ArtifactValidationError(f"isolated workspace cleanup failed: {exc}") from exc
    if provider["status"] == "pending":
        return provider
    if provider["status"] != "quiescent":
        raise ARTIFACTS.ArtifactValidationError(
            f"provider process group is not terminal and quiescent: {provider['status']}"
        )
    return provider


def require_run_request(run_dir: Path) -> dict[str, Any]:
    request = ARTIFACTS.load_json(run_dir / "host-request.json")
    ARTIFACTS.require_artifact_version(request, "host-request")
    try:
        CONFIG.load_peer_runtime().validate_request(request)
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


def guarded_run_dir(repo_root: Path, run_id: str) -> Path | None:
    """Find the active run by ID through the worktree guard, even when another host owns its run root."""

    if not CONFIG.is_safe_run_id(run_id):
        return None
    try:
        _, active = REGISTRY.read_workspace_guard(repo_root, run_id)
    except (OSError, ValueError, RuntimeError, SystemExit):
        return None
    if active is None or active.get("run_id") != run_id:
        return None
    candidate = Path(str(active["run_dir"])).expanduser()
    if not candidate.is_dir():
        return None
    return ensure_run_dir(candidate, candidate.parent, allow_external=True)


def resolve_run_context(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    """Resolve repo root, owning run root, and run directory once for a run command."""

    repo_root = CONFIG.canonical_repo_root(args.repo_root or CONFIG.default_repo_root())
    default_root = (args.run_root or CONFIG.default_run_root(repo_root)).resolve()
    run_dir = resolve_run_reference(repo_root, args.run, default_root)
    # Runs are direct children of the run root whose state index tracks them.
    return repo_root, run_dir.parent, run_dir


def resolve_run_reference(repo_root: Path, reference: str | None, run_root: Path | None = None) -> Path:
    root = (run_root or CONFIG.default_run_root(repo_root)).resolve()
    if reference:
        candidate = Path(reference).expanduser()
        if (candidate.is_absolute() or len(candidate.parts) > 1) and candidate.exists():
            return ensure_run_dir(candidate, root, allow_external=True)
        # The registry's exact active owner wins even over historical entries
        # with the same ID in this root. Prefix lookup is strictly last.
        guarded = guarded_run_dir(repo_root, reference)
        if guarded is not None:
            return guarded
        candidate = root / reference
        if candidate.exists():
            return ensure_run_dir(candidate, root)
        state = CONFIG.load_state_runtime()
        if CONFIG.is_safe_run_id(reference):
            job = state.find_job(root, reference)
            if job and job.get("run_dir"):
                return ensure_run_dir(Path(str(job["run_dir"])).expanduser(), root)
        raise SystemExit(f"unknown Agent Collab run: {reference}")

    state = CONFIG.load_state_runtime()
    job = state.find_job(root, None)
    if job and job.get("run_dir"):
        return ensure_run_dir(Path(str(job["run_dir"])).expanduser(), root)
    raise SystemExit(f"no Agent Collab runs found under {root}")


def job_patch_from_run(run_dir: Path, status: str, phase: str, **extra: Any) -> dict[str, Any]:
    request = ARTIFACTS.read_json_if_exists(run_dir / "host-request.json") or {}
    process_info = ARTIFACTS.read_json_if_exists(run_dir / "peer-process.json") or {}
    return {
        "id": str(request.get("run_id") or process_info.get("run_id") or run_dir.name),
        "run_dir": str(run_dir),
        "repo_root": str(process_info.get("repo_root") or ""),
        "host": request.get("host") or process_info.get("host"),
        "peer": request.get("peer") or process_info.get("peer"),
        "mode": request.get("mode"),
        "target": request.get("target"),
        "adjudicator_enabled": request.get("adjudicator_enabled"),
        "status": status,
        "phase": phase,
        **extra,
    }


def summarize_run(run_dir: Path) -> dict[str, Any]:
    request = require_run_request(run_dir)
    process_info = ARTIFACTS.load_json(run_dir / "peer-process.json")
    try:
        ARTIFACTS.validate_peer_process(process_info, request, run_dir)
    except ARTIFACTS.ArtifactValidationError as exc:
        raise SystemExit(f"invalid peer-process artifact in {run_dir}: {exc}") from exc
    pid = process_info.get("pid")
    host_result = ARTIFACTS.read_json_if_exists(run_dir / "host-result.json") or {}
    phase = host_result.get("phase")
    wrapper_alive = (
        process_identity_matches(process_info) and process_alive(int(pid))
        if isinstance(pid, int)
        else False
    )
    wrapper_group_active = False
    if phase not in {"ready_for_synthesis", "done"} and isinstance(process_info.get("pgid"), int):
        wrapper_group_active = CONFIG.SAFETY.process_group_alive(int(process_info["pgid"]))
    try:
        provider = ARTIFACTS.load_provider_process(run_dir, request)
        provider_status = provider["status"]
    except (OSError, ValueError, ARTIFACTS.ArtifactValidationError):
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
        peer_report = ARTIFACTS.read_json_if_exists(run_dir / "peer-report.json")
    except (OSError, ValueError) as exc:
        if process_tree_quiescent:
            raise SystemExit(f"terminal peer-report.json is invalid: {exc}") from exc
        peer_report = None
        peer_report_incomplete = True
    normalization = ARTIFACTS.read_json_if_exists(run_dir / "peer-normalization.json")
    workspace_mutation = ARTIFACTS.read_json_if_exists(run_dir / "workspace-mutation.json")
    cancel_result = ARTIFACTS.read_json_if_exists(run_dir / "cancel-result.json")
    cancel_workspace_mutation = ARTIFACTS.read_json_if_exists(run_dir / "cancel-workspace-mutation.json")
    elapsed_seconds = peer_elapsed_seconds(process_info, run_dir)
    early_cancel_blocked = (
        peer_alive
        and elapsed_seconds is not None
        and elapsed_seconds < CONFIG.MIN_PEER_WAIT_SECONDS
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
        "elapsed_seconds": ARTIFACTS.json_number(elapsed_seconds),
        "minimum_wait_seconds": CONFIG.MIN_PEER_WAIT_SECONDS,
        "minimum_wait_remaining_seconds": (
            ARTIFACTS.json_number(max(0.0, CONFIG.MIN_PEER_WAIT_SECONDS - elapsed_seconds))
            if elapsed_seconds is not None
            else None
        ),
        "early_cancel_blocked": early_cancel_blocked,
        "empty_output_guidance": "Empty peer-report.json or stderr does not imply a stalled peer while the peer process is alive.",
        "phase": phase,
        "peer_status": peer_report.get("status") if peer_report else None,
        "peer_verdict": peer_report.get("verdict") if peer_report else None,
        "peer_report_incomplete": peer_report_incomplete,
        # A failed wrapper may never write normalization; "none" is the state
        # index's value for that case, while None would be rejected.
        "normalization_source": (normalization.get("source") if normalization else None) or "none",
        "validation_status": normalization.get("validation_status") if normalization else None,
        "workspace_mutation": workspace_mutation,
        "cancel_result": cancel_result,
        "cancel_workspace_mutation": cancel_workspace_mutation,
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
            "cancel_result": file_info(run_dir / "cancel-result.json"),
            "cancel_workspace_mutation": file_info(run_dir / "cancel-workspace-mutation.json"),
            "peer_stderr": file_info(run_dir / "peer.stderr.log"),
        },
    }


def resolve_finish_wait_timeout(
    explicit_timeout_seconds: float | None,
    process_info: dict[str, Any],
) -> tuple[float, str]:
    if explicit_timeout_seconds is not None:
        if not CONFIG.MIN_PEER_WAIT_SECONDS <= explicit_timeout_seconds <= CONFIG.SAFETY.MAX_AGENT_TIMEOUT_SECONDS + CONFIG.FINISH_TIMEOUT_GRACE_SECONDS:
            raise ValueError(
                f"finish timeout must be between {CONFIG.MIN_PEER_WAIT_SECONDS} and "
                f"{CONFIG.SAFETY.MAX_AGENT_TIMEOUT_SECONDS + CONFIG.FINISH_TIMEOUT_GRACE_SECONDS} seconds"
            )
        return explicit_timeout_seconds, "explicit"

    if "peer_timeout_seconds" in process_info:
        peer_timeout = process_info["peer_timeout_seconds"]
        try:
            derived = float(peer_timeout) + CONFIG.FINISH_TIMEOUT_GRACE_SECONDS
            if not CONFIG.MIN_PEER_WAIT_SECONDS <= float(peer_timeout) <= CONFIG.SAFETY.MAX_AGENT_TIMEOUT_SECONDS:
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
        + CONFIG.FINISH_TIMEOUT_GRACE_SECONDS
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
    while True:
        if peer_report_path.exists() and peer_report_path.stat().st_size > 0:
            alive = process_identity_matches(process_info) and process_alive(pid)
            if not alive:
                return "report_ready"
            now = time.time()
            if now >= deadline:
                return "timeout"
            # Report bytes are not terminal while the wrapper is alive. The
            # wrapper owns provider-group cleanup and may still replace them;
            # process exit is the readiness boundary.
        else:
            alive = process_identity_matches(process_info) and process_alive(pid)
            now = time.time()
            if now >= deadline:
                return "timeout"
        if not alive:
            return "peer_exited"
        time.sleep(FINISH_WAIT_POLL_SECONDS)


def check_availability(args: argparse.Namespace) -> int:
    availability = CONFIG.load_availability_runtime()
    repo_root = CONFIG.canonical_repo_root(args.repo_root or CONFIG.default_repo_root())
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
                cli_version = CONFIG.require_peer_cli_version(args.peer)
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


def build_start_request(args: argparse.Namespace, settings: dict[str, Any]) -> dict[str, Any]:
    host = args.host
    peer = "claude" if host == "codex" else "codex"
    brief = CONFIG.read_text_arg(args.brief, args.brief_file)
    if not brief:
        raise SystemExit("brief or brief-file is required")
    mode = args.mode or classify_mode(args.target, brief)
    try:
        run_id = CONFIG.require_safe_run_id(args.run_id or CONFIG.utc_run_id(host, mode))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    max_local_subagents = args.max_local_subagents if args.max_local_subagents is not None else settings["max_local_subagents"]
    local_subagents_allowed = (
        bool(settings["local_subagents_allowed"])
        if args.local_subagents is None
        else bool(args.local_subagents)
    )
    adjudicator_enabled = (
        bool(settings["adjudicator_enabled"])
        if args.adjudicator is None
        else bool(args.adjudicator)
    )
    online_research = settings["online_research"] if args.online_research is None else args.online_research
    peer_model = args.peer_model if args.peer_model is not None else settings[f"{peer}_model"]
    peer_effort = args.peer_effort if args.peer_effort is not None else settings[f"{peer}_effort"]
    if peer == "claude" and CONFIG.is_fable_model(peer_model) and args.peer_model is None:
        raise SystemExit(
            "Fable is explicit-only; request it for this run with "
            "--peer-model claude-fable-5"
        )
    safe_mode = bool(settings["safe_mode"]) if args.safe_mode is None else bool(args.safe_mode)
    workspace_mode = args.workspace_mode or ("direct" if safe_mode else settings["workspace_mode"])
    if safe_mode and workspace_mode == "isolated":
        raise SystemExit("safe mode uses provider-native permissions; select --workspace-mode direct")
    if workspace_mode == "isolated" and args.edit_allowed:
        raise SystemExit("durable delegated edits require --workspace-mode direct")
    if type(max_local_subagents) is not int or not 0 <= max_local_subagents <= CONFIG.SAFETY.MAX_LOCAL_SUBAGENTS:
        raise SystemExit(f"max_local_subagents must be between 0 and {CONFIG.SAFETY.MAX_LOCAL_SUBAGENTS}")
    agent_timeout = CONFIG.configured_timeout_seconds(
        settings["agent_timeout_seconds"]
        if args.timeout_seconds is None
        else args.timeout_seconds
    )
    claude_max_turns = args.claude_max_turns
    if claude_max_turns is not None and claude_max_turns < 1:
        raise SystemExit("--claude-max-turns must be a positive integer")
    if claude_max_turns is not None and peer != "claude":
        raise SystemExit("--claude-max-turns may be used only when the resolved peer is Claude")
    if peer == "claude":
        policy_failure = CONFIG.SAFETY.claude_helper_policy_failure(peer_model, peer_effort)
        if policy_failure is not None:
            raise SystemExit(CONFIG.STRICT_JSON.dumps(policy_failure))
    request = {
        "schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION,
        "origin": host,
        "host": host,
        "peer": peer,
        "mode": mode,
        "target": args.target,
        "brief": brief,
        "edit_allowed": bool(args.edit_allowed),
        "run_id": run_id,
        "adjudicator_enabled": adjudicator_enabled,
        "local_subagents_allowed": local_subagents_allowed,
        "max_local_subagents": max_local_subagents,
        "online_research": online_research,
        "safe_mode": safe_mode,
        "workspace_mode": workspace_mode,
        "peer_model": peer_model,
        "peer_effort": peer_effort,
        "peer_timeout_seconds": ARTIFACTS.json_number(agent_timeout),
        "codex_config": list(settings["codex_config"]),
        "claude_tools": settings["claude_tools"],
        "claude_max_turns": claude_max_turns,
        "helper_enforcement": CONFIG.SAFETY.helper_enforcement(
            peer,
            local_subagents_allowed=local_subagents_allowed,
            max_local_subagents=max_local_subagents,
        ),
    }
    return request


def preflight_start(request: dict[str, Any], repo_root: Path) -> str:
    peer = request["peer"]
    peer_model, peer_effort = request["peer_model"], request["peer_effort"]
    safe_mode, workspace_mode = request["safe_mode"], request["workspace_mode"]
    availability_runtime = CONFIG.load_availability_runtime()
    # Codex read-only sandboxing uses bubblewrap on Linux. Claude's plan
    # permission mode is provider-native and does not use this backend.
    if safe_mode and peer == "codex":
        sandbox = CONFIG.SAFETY.sandbox_preflight(repo_root, dict(os.environ))
        if sandbox["status"] == "sandbox_unavailable":
            raise SystemExit(
                CONFIG.STRICT_JSON.dumps(
                    {
                        "schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION,
                        "status": "sandbox_unavailable",
                        "peer": peer,
                        "safe_mode": True,
                        "backend": sandbox["backend"],
                        "details": sandbox["details"],
                    }
                )
            )
    if workspace_mode == "isolated":
        isolation = CONFIG.load_isolation_runtime().preflight(repo_root)
        if isolation["status"] != "available":
            raise SystemExit(CONFIG.STRICT_JSON.dumps({
                "schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION,
                "status": "sandbox_unavailable",
                "peer": peer,
                "workspace_mode": workspace_mode,
                "backend": isolation["backend"],
                "details": isolation["details"],
            }))
    peer_cli_version = CONFIG.require_peer_cli_version(peer)
    availability_attestation = availability_runtime.check(
        peer,
        peer_model,
        peer_effort,
        peer_cli_version,
        repo_root,
        CONFIG.DEFAULT_AVAILABILITY_TIMEOUT_SECONDS,
    )
    if availability_attestation["status"] != "available":
        raise SystemExit(
            "Agent Collab stopped before run creation or peer launch because the requested "
            f"{peer} pair {peer_model}/{peer_effort} is not attested as available "
            f"(status={availability_attestation['status']}). No peer, helper, host-first-pass, "
            "or adjudicator was started:\n"
            + json.dumps(availability_attestation, ensure_ascii=False, indent=2, sort_keys=True)
        )
    availability_runtime.validate_result(availability_attestation, require_available=True)
    request["availability_attestation"] = availability_attestation
    return peer_cli_version


def cleanup_failed_start(process: Any, peer_identity: dict[str, Any] | None) -> dict[str, Any]:
    cleanup: dict[str, Any] = {"outcome": "not_started", "quiescent": True}
    if process is not None:
        try:
            if peer_identity is not None:
                if CONFIG.SAFETY.process_identity_matches(peer_identity):
                    cleanup = CONFIG.SAFETY.terminate_process_group(int(peer_identity["pgid"]))
                else:
                    group_alive = CONFIG.SAFETY.process_group_alive(int(peer_identity["pgid"]))
                    cleanup = {
                        "outcome": "identity_mismatch_group_live" if group_alive else "identity_mismatch_group_empty",
                        "quiescent": not group_alive,
                        "pgid": int(peer_identity["pgid"]),
                    }
            elif process.poll() is None:
                # The live Popen handle and a new session prove ownership
                # even if /proc identity capture failed.
                cleanup = CONFIG.SAFETY.terminate_process_group(int(process.pid))
            else:
                group_alive = CONFIG.SAFETY.process_group_alive(int(process.pid))
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
    return cleanup


def peer_process_artifact(request: dict[str, Any], resolved: dict[str, Any], repo_root: Path,
                          run_dir: Path, guard_path: Path, owner: dict[str, Any],
                          peer_identity: dict[str, Any], started_at_epoch: float, peer_cli_version: str) -> dict[str, Any]:
    return {
        "schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION,
        "pid": peer_identity["pid"],
        "pgid": peer_identity["pgid"],
        "process_identity": peer_identity,
        "run_id": request["run_id"],
        "run_dir": str(run_dir),
        "repo_root": str(repo_root),
        "host": request["host"],
        "peer": request["peer"],
        "adjudicator_enabled": request["adjudicator_enabled"],
        "started_at": ARTIFACTS.utc_timestamp(started_at_epoch),
        "started_at_epoch": started_at_epoch,
        "peer_timeout_seconds": request["peer_timeout_seconds"],
        "peer_cli_version": peer_cli_version,
        "workspace_guard": str(guard_path),
        "owner_token": owner["owner_token"],
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


def spawn_peer_wrapper(request_path: Path, repo_root: Path, run_dir: Path,
                       peer_stdout: Any, peer_stderr: Any, startup_gate_read: int) -> Any:
    env = dict(os.environ)
    env["AGENT_COLLAB_RUN_DIR"] = str(run_dir)
    popen_options: dict[str, Any] = {}
    startup_gate_token = CONFIG.SAFETY.pass_gate(startup_gate_read, popen_options)
    return CONFIG.SAFETY.popen_group(
        [
            sys.executable,
            str(CONFIG.runtime_dir() / "peer.py"),
            str(request_path),
            "--repo-root",
            str(repo_root),
            "--raw-output",
            str(run_dir / "peer.raw.json"),
            "--normalization-output",
            str(run_dir / "peer-normalization.json"),
            "--startup-gate-fd",
            str(startup_gate_token),
        ],
        detached=True,
        cwd=repo_root,
        stdin=subprocess.DEVNULL,
        stdout=peer_stdout,
        stderr=peer_stderr,
        env=env,
        text=True,
        encoding="utf-8",
        **popen_options,
    )


def launch_start(repo_root: Path, run_root: Path, resolved: dict[str, Any], request: dict[str, Any], peer_cli_version: str) -> int:
    run_id, host, peer = request["run_id"], request["host"], request["peer"]
    workspace_mode = request["workspace_mode"]
    adjudicator_enabled = request["adjudicator_enabled"]
    state = CONFIG.load_state_runtime()
    run_dir = run_root / run_id
    owner = {"run_id": run_id, "repo_root": str(repo_root), "run_dir": str(run_dir),
             "host": host, "owner_token": secrets.token_hex(16)}
    guard_path = REGISTRY.acquire_workspace_guard(repo_root, run_id, host, run_dir, owner_token=owner["owner_token"])
    run_dir_created = False
    peer_stdout = None
    peer_stderr = None
    process = None
    peer_identity = None
    state_job_created = False
    startup_gate_read = None
    startup_gate_write = None
    try:
        run_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
        os.chmod(run_dir, 0o700)
        run_dir_created = True

        request_path = run_dir / "host-request.json"
        write_json(request_path, request)
        run_snapshot(repo_root, run_dir / "before.snapshot", ignored_paths=[run_dir], workspace_mode=workspace_mode)
        peer_stdout = CONFIG.STRICT_JSON.open_private_text(run_dir / "peer-report.json")
        peer_stderr = CONFIG.STRICT_JSON.open_private_text(run_dir / "peer.stderr.log")
        started_at_epoch = time.time()
        startup_gate_read, startup_gate_write = os.pipe()
        process = spawn_peer_wrapper(request_path, repo_root, run_dir, peer_stdout, peer_stderr, startup_gate_read)
        try:
            peer_identity = CONFIG.SAFETY.process_identity(process.pid)
        except (OSError, RuntimeError, ValueError) as exc:
            raise RuntimeError(
                "peer runtime exited before its exact process identity could be recorded"
            ) from exc
        process_info = peer_process_artifact(request, resolved, repo_root, run_dir, guard_path, owner,
                                             peer_identity, started_at_epoch, peer_cli_version)
        write_json(run_dir / "peer-process.json", process_info)
        REGISTRY.update_workspace_guard(
            repo_root,
            run_id,
            owner=owner,
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
                "mode": request["mode"],
                "target": request["target"],
                "status": "running",
                "phase": "peer_running",
                "pid": process.pid,
                "adjudicator_enabled": adjudicator_enabled,
                "edit_allowed": request["edit_allowed"],
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
        cleanup = cleanup_failed_start(process, peer_identity)
        if cleanup.get("quiescent") is not True:
            if run_dir_created:
                try:
                    write_json(
                        run_dir / "startup-cleanup-failure.json",
                        {
                            "schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION,
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
            REGISTRY.release_workspace_guard(repo_root, run_id, owner=owner)
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

def start(args: argparse.Namespace) -> int:
    CONFIG.require_supported_platform()
    peer_runtime = CONFIG.load_peer_runtime()
    if peer_runtime.nested_invocation_requested(dict(os.environ)):
        raise SystemExit("nested Agent Collab invocation refused by the cross-agent depth guard")
    repo_root = CONFIG.canonical_repo_root(args.repo_root or CONFIG.default_repo_root())
    run_root = (args.run_root or CONFIG.default_run_root(repo_root)).resolve()
    state = CONFIG.load_state_runtime()
    try:
        state.load_state(run_root)
    except (OSError, ValueError) as exc:
        state_path = run_root / "state.json"
        raise SystemExit(
            "invalid v2 Agent Collab state; refusing to launch a peer: "
            f"{exc}\nArchive {state_path} for evidence and remove or rename it, or select a fresh "
            "--run-root. Agent Collab will not migrate or coerce legacy state."
        ) from exc
    REGISTRY.require_workspace_guard_available(repo_root)
    resolved = CONFIG.resolve_settings(repo_root)
    CONFIG.require_valid_settings(resolved)
    request = build_start_request(args, resolved["settings"])
    peer_cli_version = preflight_start(request, repo_root)
    peer_runtime.validate_request(request, require_fresh_attestation=True)
    return launch_start(repo_root, run_root, resolved, request, peer_cli_version)


def review_documents(run_dir: Path) -> dict[str, Any]:
    return {name: ARTIFACTS.read_json_if_exists(run_dir / name) for name in CONFIG.load_review_runtime().EVIDENCE_FILES}


def ingest_finish_inputs(args: argparse.Namespace, run_dir: Path, request: dict[str, Any], process_info: dict[str, Any]) -> None:
    inputs = [("first_pass", "host-first-pass.json", {"summary", "claims"}, ARTIFACTS.validate_host_first_pass),
              ("helpers", "helper-reports.json", {"reports"}, ARTIFACTS.validate_helper_evidence),
              ("adjudicator", "adjudicator-report.json", {"status", "summary", "false_positives",
                  "claims_needing_verification", "recommended_verdict", "claims"}, ARTIFACTS.validate_adjudicator_report)]
    supplied = [item for item in inputs if getattr(args, item[0], None) is not None]
    if not supplied:
        return
    with REGISTRY.run_operation_lock(run_dir):
        REGISTRY.require_workspace_guard_owner(Path(process_info["repo_root"]), request["run_id"], owner=process_info)
        current = ARTIFACTS.read_json_if_exists(run_dir / "host-result.json")
        if (run_dir / "cancel-result.json").exists() or (current and current.get("phase") == "done"):
            raise ARTIFACTS.ArtifactValidationError("cannot replace evidence of a terminal run")
        pending = []
        for option, filename, allowed, validate in supplied:
            value = ARTIFACTS.load_json(getattr(args, option))
            required = allowed - ({"claims"} if option == "adjudicator" else set())
            if not required <= set(value) <= allowed:
                raise ARTIFACTS.ArtifactValidationError(f"{option} input keys must be {sorted(allowed)}; only adjudicator claims are optional")
            if option == "adjudicator" and (not request["adjudicator_enabled"] or value.get("status") == "advisory_pending"):
                raise ARTIFACTS.ArtifactValidationError("adjudicator input requires enabled adjudication and an actual report, not advisory_pending")
            artifact = {"schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION, "run_id": request["run_id"], **value}
            validate(artifact, request)
            pending.append((run_dir / filename, artifact))
        for path, artifact in pending:
            write_json(path, artifact)


def finish(args: argparse.Namespace) -> int:
    _, run_root, run_dir = resolve_run_context(args)
    state = CONFIG.load_state_runtime()
    request = require_run_request(run_dir)
    process_info = ARTIFACTS.load_json(run_dir / "peer-process.json")
    ARTIFACTS.validate_peer_process(process_info, request, run_dir)
    ingest_finish_inputs(args, run_dir, request, process_info)
    host_first_pass = run_dir / "host-first-pass.json"
    if not host_first_pass.exists():
        raise SystemExit(f"missing required independent host analysis: {host_first_pass}")
    host_report = ARTIFACTS.load_json(host_first_pass)
    try:
        ARTIFACTS.validate_host_first_pass(host_report, request)
    except ARTIFACTS.ArtifactValidationError as exc:
        raise SystemExit(f"invalid independent host analysis {host_first_pass}: {exc}") from exc
    repo_root = Path(process_info["repo_root"]).resolve()
    _, active_guard = REGISTRY.require_workspace_guard_owner(repo_root, request["run_id"], owner=process_info)
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
    with REGISTRY.run_operation_lock(run_dir):
        return _finish_after_wait_locked(
            state=state,
            run_root=run_root,
            run_dir=run_dir,
            request=request,
            host_report=host_report,
            process_info=process_info,
            repo_root=repo_root,
            peer_report_path=peer_report_path,
            finish_wait_seconds=finish_wait_seconds,
            finish_timeout_source=finish_timeout_source,
            hard_deadline=hard_deadline,
            wait_status=wait_status,
        )


def _finish_after_wait_locked(
    *,
    state: Any,
    run_root: Path,
    run_dir: Path,
    request: dict[str, Any],
    host_report: dict[str, Any],
    process_info: dict[str, Any],
    repo_root: Path,
    peer_report_path: Path,
    finish_wait_seconds: float,
    finish_timeout_source: str,
    hard_deadline: float,
    wait_status: str,
) -> int:
    current_request = require_run_request(run_dir)
    if current_request != request:
        raise SystemExit("host-request changed while finish was waiting; refusing finalization")
    current_process = ARTIFACTS.load_json(run_dir / "peer-process.json")
    if current_process != process_info:
        raise SystemExit("peer-process changed while finish was waiting; refusing finalization")
    current_host_report = ARTIFACTS.load_json(run_dir / "host-first-pass.json")
    if current_host_report != host_report:
        raise SystemExit("host-first-pass changed while finish was waiting; rerun finish")
    if (run_dir / "cancel-result.json").exists():
        raise SystemExit("run was cancelled while finish was waiting; terminal evidence was preserved")
    current_job = state.find_job(run_root, request["run_id"])
    if current_job is not None and current_job.get("status") in {"completed", "failed", "cancelled"}:
        raise SystemExit(
            f"run is already terminal with status {current_job['status']}; finish will not reactivate it"
        )
    existing_host_result = ARTIFACTS.read_json_if_exists(run_dir / "host-result.json")
    if existing_host_result is not None and existing_host_result.get("phase") == "done":
        raise SystemExit("run is already complete; finish will not rewrite terminal evidence")

    _, active_guard = REGISTRY.require_workspace_guard_owner(repo_root, request["run_id"], owner=process_info)
    wrapper_quiescence_proven = bool(
        active_guard is not None
        and active_guard["phase"] == "ready_for_synthesis"
        and active_guard["peer_identity"] == process_info["process_identity"]
    )
    if (
        active_guard is not None
        and active_guard["phase"] == "ready_for_synthesis"
        and not wrapper_quiescence_proven
    ):
        raise SystemExit("ready-for-synthesis workspace guard contains an invalid wrapper-quiescence proof")
    pid = int(process_info["pid"])

    if wait_status == "timeout" and time.time() < hard_deadline:
        output = {
            "schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION,
            "run_id": request["run_id"],
            "run_dir": str(run_dir),
            "status": "peer_running",
            "phase": "waiting_for_peer",
            "peer_pid": pid,
            "finish_wait_seconds": ARTIFACTS.json_number(finish_wait_seconds),
            "finish_timeout_source": finish_timeout_source,
            "peer_report": str(peer_report_path),
            "message": "Peer is still running and produced no stable complete report before finish timeout.",
        }
        try:
            ARTIFACTS.validate_host_result(output, request, run_dir)
        except ARTIFACTS.ArtifactValidationError as exc:
            raise SystemExit(f"refusing to persist an invalid waiting host result: {exc}") from exc
        state.upsert_job(
            run_root,
            {
                **job_patch_from_run(run_dir, "running", "waiting_for_peer"),
                "pid": pid,
                "finish_wait_seconds": ARTIFACTS.json_number(finish_wait_seconds),
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
        except ARTIFACTS.ArtifactValidationError:
            cleanup = terminate_tracked_process_tree(process_info, run_dir, request)
            if cleanup["quiescent"] is not True:
                raise SystemExit(
                    "hard peer deadline elapsed but the tracked process tree could not be proven quiescent; "
                    "the workspace guard remains active:\n"
                    + json.dumps(cleanup, ensure_ascii=False, indent=2, sort_keys=True)
                )
            peer_runtime = CONFIG.load_peer_runtime()
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
        except ARTIFACTS.ArtifactValidationError as exc:
            raise SystemExit(
                f"peer report is not terminal because the tracked process tree is not quiescent: {exc}"
            ) from exc

    if not peer_report_path.exists() or peer_report_path.stat().st_size == 0:
        failure = {
            "schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION,
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

    peer_runtime = CONFIG.load_peer_runtime()
    peer_report, validation_status = ARTIFACTS.normalize_peer_report_from_artifacts(peer_runtime, run_dir, request)

    adjudicator_path = run_dir / "adjudicator-report.json"
    adjudicator: dict[str, Any] | None = None
    if request["adjudicator_enabled"]:
        if adjudicator_path.exists():
            adjudicator = ARTIFACTS.load_json(adjudicator_path)
        else:
            adjudicator = ARTIFACTS.default_adjudicator_report(request, peer_report)
            write_json(adjudicator_path, adjudicator)
        try:
            ARTIFACTS.validate_adjudicator_report(adjudicator, request)
        except ARTIFACTS.ArtifactValidationError as exc:
            raise SystemExit(f"invalid adjudicator report {adjudicator_path}: {exc}") from exc
    elif adjudicator_path.exists():
        raise SystemExit(
            "adjudicator-report.json is forbidden because adjudication is disabled in the immutable host request"
        )

    try:
        helper_claims, helper_digest = ARTIFACTS.load_helper_evidence(run_dir, request)
        claim_matrix = ARTIFACTS.build_claim_matrix(
            request, host_report, peer_report, helper_claims, adjudicator, helper_digest
        )
    except ARTIFACTS.ArtifactValidationError as exc:
        raise SystemExit(f"invalid helper or claim-matrix artifact in {run_dir}: {exc}") from exc
    write_json(run_dir / "claim-matrix.json", claim_matrix)

    try:
        workspace_mutation = record_host_workspace_mutation(repo_root, run_dir, request)
    except (OSError, ValueError, ARTIFACTS.ArtifactValidationError) as exc:
        raise SystemExit(f"host workspace mutation verification failed: {exc}") from exc
    result = {
        "schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION,
        "run_id": request["run_id"],
        "run_dir": str(run_dir),
        "peer_status": peer_report.get("status"),
        "validation_status": validation_status,
        "finish_wait_seconds": ARTIFACTS.json_number(finish_wait_seconds),
        "finish_timeout_source": finish_timeout_source,
        "normalization": str(run_dir / "peer-normalization.json"),
        "claim_matrix": str(run_dir / "claim-matrix.json"),
        "claim_matrix_sha256": ARTIFACTS.claim_matrix_sha256(claim_matrix),
        "adjudicator_report": str(adjudicator_path) if adjudicator is not None else None,
        "workspace_mutation": workspace_mutation,
        "workspace_mutation_sha256": ARTIFACTS.workspace_mutation_sha256(workspace_mutation),
    }
    ready_result = {**result, "phase": "ready_for_synthesis"}
    try:
        ARTIFACTS.validate_host_result(ready_result, request, run_dir)
    except ARTIFACTS.ArtifactValidationError as exc:
        raise SystemExit(f"refusing to persist an invalid ready host result: {exc}") from exc
    state.upsert_job(
        run_root,
        {
            **job_patch_from_run(run_dir, "synthesizing", "ready_for_synthesis"),
            "peer_status": peer_report.get("status"),
            "peer_verdict": peer_report.get("verdict"),
            "validation_status": validation_status,
            "normalization_source": (
                (ARTIFACTS.read_json_if_exists(run_dir / "peer-normalization.json") or {}).get("source") or "none"
            ),
            "workspace_mutation": bool(workspace_mutation),
            "summary": peer_report.get("summary"),
        },
    )
    REGISTRY.update_workspace_guard(
        repo_root,
        request["run_id"],
        owner=process_info,
        phase="ready_for_synthesis",
        peer_identity=process_info["process_identity"],
    )
    write_json(run_dir / "host-result.json", ready_result)
    review_paths = CONFIG.load_review_runtime().prepare(run_dir, process_info, review_documents(run_dir))
    print(json.dumps({**ready_result, **review_paths}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def complete(args: argparse.Namespace) -> int:
    context = resolve_run_context(args)
    with REGISTRY.run_operation_lock(context[2]):
        return _complete_locked(args, context)


def _complete_locked(args: argparse.Namespace, context: tuple[Path, Path, Path]) -> int:
    _, run_root, run_dir = context
    request = require_run_request(run_dir)
    process_info = ARTIFACTS.load_json(run_dir / "peer-process.json")
    try:
        ARTIFACTS.validate_peer_process(process_info, request, run_dir)
    except ARTIFACTS.ArtifactValidationError as exc:
        raise SystemExit(f"invalid peer-process artifact in {run_dir}: {exc}") from exc
    repo_root = Path(process_info["repo_root"]).resolve()

    host_result_path = run_dir / "host-result.json"
    host_result = ARTIFACTS.load_json(host_result_path)
    try:
        ARTIFACTS.validate_host_result(host_result, request, run_dir)
    except ARTIFACTS.ArtifactValidationError as exc:
        raise SystemExit(f"invalid host-result artifact in {run_dir}: {exc}") from exc
    phase = host_result["phase"]
    if phase not in {"ready_for_synthesis", "done"}:
        raise SystemExit("run is not ready to complete; run finish and synthesize the evidence first")
    if phase == "ready_for_synthesis":
        _, active_guard = REGISTRY.require_workspace_guard_owner(repo_root, request["run_id"], owner=process_info)
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
        except ARTIFACTS.ArtifactValidationError as exc:
            raise SystemExit(
                f"cannot complete while the tracked peer process tree is active: {exc}"
            ) from exc
        try:
            workspace_mutation = record_host_workspace_mutation(repo_root, run_dir, request)
        except (OSError, ValueError, ARTIFACTS.ArtifactValidationError) as exc:
            raise SystemExit(f"final host workspace mutation verification failed: {exc}") from exc
        if host_result["workspace_mutation"] != workspace_mutation:
            host_result = {
                **host_result,
                "workspace_mutation": workspace_mutation,
                "workspace_mutation_sha256": ARTIFACTS.workspace_mutation_sha256(workspace_mutation),
            }
            try:
                ARTIFACTS.validate_host_result(host_result, request, run_dir)
            except ARTIFACTS.ArtifactValidationError as exc:
                raise SystemExit(f"refusing to persist an invalid refreshed host result: {exc}") from exc
            write_json(host_result_path, host_result)
            raise SystemExit(
                "workspace mutation evidence changed after finish; host-result.json was refreshed. "
                "Run finish, inspect the new review revision, and resynthesize before completing."
            )
    elif phase == "done":
        _, active_guard = REGISTRY.read_workspace_guard(repo_root, request["run_id"])
        if active_guard is not None:
            REGISTRY.validate_guard_owner(active_guard, process_info)
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
            except ARTIFACTS.ArtifactValidationError as exc:
                raise SystemExit(
                    f"completed run guard cannot be released while provider state is unproven: {exc}"
                ) from exc

    if getattr(args, "synthesis", None) is not None:
        judgments = CONFIG.load_review_runtime().judgments(run_dir, args.synthesis, process_info, review_documents(run_dir))
        synthesis = {"schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION, "run_id": request["run_id"],
                     **judgments, "claim_matrix_sha256": host_result["claim_matrix_sha256"],
                     "workspace_mutation_sha256": host_result["workspace_mutation_sha256"], "final_answer_ready": True}
        ARTIFACTS.validate_host_synthesis(synthesis, request)
        if phase == "done":
            if ARTIFACTS.load_json(run_dir / "host-synthesis.json") != synthesis:
                raise ARTIFACTS.ArtifactValidationError("cannot rewrite a completed synthesis")
        else:
            write_json(run_dir / "host-synthesis.json", synthesis)

    peer_runtime = CONFIG.load_peer_runtime()
    try:
        peer_report = ARTIFACTS.validate_completion_artifacts(run_dir, request, host_result, peer_runtime)
    except (OSError, ValueError, getattr(peer_runtime, "PeerReportValidationError")) as exc:
        raise SystemExit(f"cannot complete run with invalid v2 synthesis artifacts: {exc}") from exc

    state_status = "completed" if peer_report["status"] == "ok" else "failed"
    completed_at = (
        host_result["completed_at"]
        if phase == "done"
        else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    state = CONFIG.load_state_runtime()
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
        guard_path, active = REGISTRY.read_workspace_guard(repo_root, request["run_id"])
        if active is None:
            output = {
                "schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION,
                "run_id": request["run_id"],
                "run_dir": str(run_dir),
                "status": "already_complete",
                "workspace_guard": str(guard_path),
            }
            print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        REGISTRY.validate_guard_owner(active, process_info)
        REGISTRY.release_workspace_guard(repo_root, request["run_id"], owner=process_info)
        output = {
            "schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION,
            "run_id": request["run_id"],
            "run_dir": str(run_dir),
            "status": "already_complete",
            "workspace_guard_released": str(guard_path),
        }
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    done_result = {**host_result, "phase": "done", "completed_at": completed_at}
    try:
        ARTIFACTS.validate_host_result(done_result, request, run_dir)
    except ARTIFACTS.ArtifactValidationError as exc:
        raise SystemExit(f"refusing to persist an invalid completed host result: {exc}") from exc
    write_json(host_result_path, done_result)
    guard_path = REGISTRY.release_workspace_guard(repo_root, request["run_id"], owner=process_info)
    output = {
        "schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION,
        "run_id": request["run_id"],
        "run_dir": str(run_dir),
        "status": state_status,
        "workspace_guard_released": str(guard_path),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def recover(args: argparse.Namespace) -> int:
    repo_root = CONFIG.canonical_repo_root(args.repo_root or CONFIG.default_repo_root())
    path = REGISTRY.recover_workspace_guard(repo_root, args.run)
    print(CONFIG.STRICT_JSON.dumps({"status": "recovered", "run_id": args.run, "registry": str(path)}))
    return 0


def status(args: argparse.Namespace) -> int:
    repo_root = CONFIG.canonical_repo_root(args.repo_root or CONFIG.default_repo_root())
    run_root = (args.run_root or CONFIG.default_run_root(repo_root)).resolve()
    state = CONFIG.load_state_runtime()

    if getattr(args, "active", False):
        if args.run or args.wait or args.all:
            raise SystemExit("status --active cannot be combined with a run, --wait, or --all")
        path, entries = REGISTRY.read_workspace_guards(repo_root)
        print(CONFIG.STRICT_JSON.dumps({"registry": str(path), "runs": list(entries.values())}))
        return 0

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
    repo_root = CONFIG.canonical_repo_root(args.repo_root or CONFIG.default_repo_root())
    run_root = (args.run_root or CONFIG.default_run_root(repo_root)).resolve()
    run_dir = resolve_run_reference(repo_root, args.run, run_root)
    if getattr(args, "format", "json") == "markdown":
        if args.review is None:
            raise SystemExit("result --format markdown requires --review DIR from finish")
        with REGISTRY.run_operation_lock(run_dir):
            process_info = ARTIFACTS.load_json(run_dir / "peer-process.json")
            ARTIFACTS.validate_peer_process(process_info, require_run_request(run_dir), run_dir)
            documents = review_documents(run_dir)
            review = CONFIG.load_review_runtime()
            review.verify(run_dir, args.review, process_info, documents)
            print(review.markdown(documents))
        return 0
    if getattr(args, "review", None) is not None:
        raise SystemExit("--review requires --format markdown")
    process_info = ARTIFACTS.read_json_if_exists(run_dir / "peer-process.json")
    output = {
        "run": summarize_run(run_dir),
        "process": process_info,
        "request": ARTIFACTS.read_json_if_exists(run_dir / "host-request.json"),
        "host_first_pass": ARTIFACTS.read_json_if_exists(run_dir / "host-first-pass.json"),
        "host_synthesis": ARTIFACTS.read_json_if_exists(run_dir / "host-synthesis.json"),
        "peer_normalization": ARTIFACTS.read_json_if_exists(run_dir / "peer-normalization.json"),
        "peer_report": ARTIFACTS.read_json_if_exists(run_dir / "peer-report.json"),
        "claim_matrix": ARTIFACTS.read_json_if_exists(run_dir / "claim-matrix.json"),
        "adjudicator_report": ARTIFACTS.read_json_if_exists(run_dir / "adjudicator-report.json"),
        "host_result": ARTIFACTS.read_json_if_exists(run_dir / "host-result.json"),
        "workspace_mutation": ARTIFACTS.read_json_if_exists(run_dir / "workspace-mutation.json"),
        "cancel_result": ARTIFACTS.read_json_if_exists(run_dir / "cancel-result.json"),
        "cancel_workspace_mutation": ARTIFACTS.read_json_if_exists(run_dir / "cancel-workspace-mutation.json"),
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


ORPHAN_STARTUP_GRACE_SECONDS = 24 * 60 * 60


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


def resolve_history_retain(args: argparse.Namespace, default_retained: int = CONFIG.DEFAULT_HISTORY_RETAINED_RUNS) -> int:
    if getattr(args, "all", False):
        return 0
    if getattr(args, "retain", None) is not None:
        return max(int(args.retain), 0)
    return max(int(default_retained), 0)


def build_clear_history_output(args: argparse.Namespace, default_retained: int = CONFIG.DEFAULT_HISTORY_RETAINED_RUNS) -> tuple[int, dict[str, Any] | None]:
    repo_root = CONFIG.canonical_repo_root(args.repo_root or CONFIG.default_repo_root())
    run_root = (args.run_root or CONFIG.default_run_root(repo_root)).resolve()
    state_runtime = CONFIG.load_state_runtime()
    active_statuses = state_runtime.ACTIVE_STATUSES
    candidates = history_candidates(run_root, state_runtime)
    _, active_guards = REGISTRY.read_workspace_guards(repo_root)
    guarded_run_ids = set(active_guards)
    guarded_run_dirs = {str(Path(str(guard["run_dir"])).resolve()) for guard in active_guards.values()}

    owner_guards: dict[str, Any] = {}

    def owner_guard_holds(candidate: dict[str, Any]) -> bool:
        """Preserve runs another worktree's guard owns, or whose owner is unknown."""

        run_dir = Path(candidate["run_dir"])
        try:
            process_info = ARTIFACTS.read_json_if_exists(run_dir / "peer-process.json")
        except (OSError, ValueError, SystemExit):
            return True
        owner = process_info.get("repo_root") if isinstance(process_info, dict) else None
        if not isinstance(owner, str) or not owner:
            # A startup records ownership only after creating the run directory;
            # keep young orphans until a start could no longer be in progress.
            if candidate["status"] != "orphan":
                return False
            try:
                age = time.time() - run_dir.stat().st_mtime
            except OSError:
                return True
            return age < ORPHAN_STARTUP_GRACE_SECONDS
        if owner not in owner_guards:
            try:
                owner_guards[owner] = REGISTRY.read_workspace_guards(Path(owner))[1]
            except (OSError, ValueError, RuntimeError, SystemExit):
                owner_guards[owner] = "indeterminate"
        guard = owner_guards[owner]
        if guard == "indeterminate":
            return True
        return candidate["run_id"] in guard

    def is_guarded(candidate: dict[str, Any]) -> bool:
        if (candidate["run_id"] in guarded_run_ids
                or str(Path(candidate["run_dir"]).resolve()) in guarded_run_dirs):
            return True
        return owner_guard_holds(candidate)

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
    repo_root = CONFIG.canonical_repo_root(args.repo_root or CONFIG.default_repo_root())
    resolved = CONFIG.resolve_settings(repo_root)
    CONFIG.require_valid_settings(resolved)
    settings = resolved["settings"]
    status, output = build_clear_history_output(args, int(settings.get("history_retained_runs", CONFIG.DEFAULT_HISTORY_RETAINED_RUNS)))
    if output is not None:
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return status


def terminate_tracked_process_tree(
    process_info: dict[str, Any],
    run_dir: Path,
    request: dict[str, Any],
) -> dict[str, Any]:
    try:
        provider = ARTIFACTS.load_provider_process(run_dir, request)
    except (OSError, ValueError, ARTIFACTS.ArtifactValidationError) as exc:
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
        and CONFIG.SAFETY.process_identity_state(provider["process_identity"]) == "unknown"
    ):
        # An empty-group answer cannot prove quiescence for a root whose
        # identity is unreadable.
        provider_cleanup = {"outcome": "identity_unknown", "quiescent": False}
    elif (
        provider["process_identity"] is not None
        and CONFIG.SAFETY.process_identity_matches(provider["process_identity"])
    ):
        provider_pgid = int(provider["pgid"])
        provider_cleanup = CONFIG.SAFETY.terminate_process_group(provider_pgid)
        if provider_cleanup["quiescent"] and provider["process_identity"] is not None:
            provider = {
                **provider,
                "status": "quiescent",
                "cleanup_outcome": str(provider_cleanup["outcome"]),
                "completed_at": ARTIFACTS.utc_timestamp(),
            }
            write_json(run_dir / "provider-process.json", provider)
    else:
        provider_group_alive = CONFIG.SAFETY.process_group_alive(int(provider["pgid"]))
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
                "completed_at": ARTIFACTS.utc_timestamp(),
            }
            write_json(run_dir / "provider-process.json", provider)

    if (
        request["workspace_mode"] == "isolated"
        and provider_cleanup.get("quiescent")
        and not process_identity_matches(process_info)
    ):
        try:
            CONFIG.load_isolation_runtime().cleanup_state(run_dir / "isolation-state.json")
        except (OSError, ValueError, RuntimeError) as exc:
            provider_cleanup = {
                "outcome": f"isolated_workspace_cleanup_failed: {exc}",
                "quiescent": False,
            }
    wrapper_pgid = int(process_info["pgid"])
    wrapper_state = CONFIG.SAFETY.process_identity_state(process_info.get("process_identity"))
    if wrapper_state == "matching":
        wrapper_cleanup = CONFIG.SAFETY.terminate_process_group(wrapper_pgid)
    elif wrapper_state == "unknown":
        wrapper_cleanup = {"outcome": "identity_unknown", "quiescent": False}
    else:
        wrapper_cleanup = {
            "outcome": "identity_mismatch",
            "quiescent": not CONFIG.SAFETY.process_group_alive(wrapper_pgid),
        }
    if wrapper_cleanup.get("quiescent"):
        try:
            refreshed_provider = ARTIFACTS.load_provider_process(run_dir, request)
        except (OSError, ValueError, ARTIFACTS.ArtifactValidationError):
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
    if (
        request["workspace_mode"] == "isolated"
        and wrapper_cleanup.get("quiescent")
        and provider_cleanup.get("quiescent")
    ):
        # A killed wrapper may not have finished its own unmount; the idempotent
        # receipt cleanup must succeed before the guard can be released.
        try:
            CONFIG.load_isolation_runtime().cleanup_state(run_dir / "isolation-state.json")
        except (OSError, ValueError, RuntimeError) as exc:
            provider_cleanup = {
                "outcome": f"isolated_workspace_cleanup_failed: {exc}",
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
    return CONFIG.SAFETY.process_identity_matches(process_info.get("process_identity"))


def cancel(args: argparse.Namespace) -> int:
    context = resolve_run_context(args)
    with REGISTRY.run_operation_lock(context[2]):
        return _cancel_locked(args, context)


def _cancel_locked(args: argparse.Namespace, context: tuple[Path, Path, Path]) -> int:
    repo_root, run_root, run_dir = context
    process_info = ARTIFACTS.load_json(run_dir / "peer-process.json")
    request = require_run_request(run_dir)
    try:
        ARTIFACTS.validate_peer_process(process_info, request, run_dir)
    except ARTIFACTS.ArtifactValidationError as exc:
        raise SystemExit(f"invalid peer-process artifact in {run_dir}: {exc}") from exc
    peer_repo_root = Path(process_info["repo_root"]).resolve()
    cancel_result_path = run_dir / "cancel-result.json"
    state = CONFIG.load_state_runtime()

    if cancel_result_path.exists():
        existing = ARTIFACTS.load_json(cancel_result_path)
        try:
            ARTIFACTS.validate_cancel_result(existing, request, run_dir)
        except ARTIFACTS.ArtifactValidationError as exc:
            raise SystemExit(f"invalid immutable cancel result {cancel_result_path}: {exc}") from exc
        if ARTIFACTS.preserved_artifact_hashes(run_dir) != existing["preserved_artifacts"]:
            raise SystemExit(
                "preserved run evidence changed after cancellation; refusing an idempotent guard transition"
            )
        guard_path, active_guard = REGISTRY.read_workspace_guard(peer_repo_root, request["run_id"])
        released: str | None = None
        if active_guard is not None:
            REGISTRY.validate_guard_owner(active_guard, process_info)
            wrapper_quiescence_proven = bool(
                active_guard["phase"] == "ready_for_synthesis"
                and active_guard["peer_identity"] == process_info["process_identity"]
            )
            try:
                require_process_tree_quiescent(
                    process_info,
                    run_dir,
                    request,
                    wrapper_quiescence_proven=wrapper_quiescence_proven,
                )
            except ARTIFACTS.ArtifactValidationError as exc:
                raise SystemExit(
                    f"immutable cancellation exists but process-tree quiescence is unproven: {exc}"
                ) from exc
            released = str(REGISTRY.release_workspace_guard(peer_repo_root, request["run_id"], owner=process_info))
        state.upsert_job(
            run_root,
            {
                **job_patch_from_run(run_dir, "cancelled", "cancelled"),
                "pid": None,
                "completed_at": existing["completed_at"],
                "cancel": {
                    key: existing[key]
                    for key in (
                        "forced",
                        "reason",
                        "elapsed_seconds",
                        "minimum_wait_seconds",
                        "before_minimum_wait",
                    )
                },
                "cancel_result": str(cancel_result_path),
            },
        )
        output = {
            "schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION,
            "run_id": request["run_id"],
            "run_dir": str(run_dir),
            "outcome": "already_cancelled",
            "cancel_result": str(cancel_result_path),
            "workspace_guard": str(guard_path),
            "workspace_guard_released": released,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    _, active_guard = REGISTRY.require_workspace_guard_owner(peer_repo_root, request["run_id"], owner=process_info)
    if active_guard is None or active_guard["run_id"] != request["run_id"]:
        raise SystemExit("active Agent Collab workspace guard disappeared during cancellation")
    wrapper_quiescence_proven = bool(
        active_guard["phase"] == "ready_for_synthesis"
        and active_guard["peer_identity"] == process_info["process_identity"]
    )
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
        and elapsed_seconds < CONFIG.MIN_PEER_WAIT_SECONDS
    )

    try:
        require_process_tree_quiescent(
            process_info,
            run_dir,
            request,
            wrapper_quiescence_proven=wrapper_quiescence_proven,
        )
    except ARTIFACTS.ArtifactValidationError:
        pass
    else:
        peer_runtime = CONFIG.load_peer_runtime()
        if ARTIFACTS.valid_terminal_peer_report(peer_runtime, run_dir, request):
            output = {
                "schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION,
                "run_id": request["run_id"],
                "run_dir": str(run_dir),
                "pid": pid,
                "outcome": "already_terminal",
                "message": (
                    "A valid terminal peer report already exists. The workspace guard was retained; "
                    "run finish and complete so the evidence is not converted into a cancellation."
                ),
            }
            print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
            return 1

    if before_minimum_wait and not force_before_min_wait:
        output = {
            "run_id": request["run_id"],
            "run_dir": str(run_dir),
            "pid": pid,
            "outcome": "refused",
            "reason": "minimum_wait_not_elapsed",
            "elapsed_seconds": ARTIFACTS.json_number(elapsed_seconds),
            "minimum_wait_seconds": CONFIG.MIN_PEER_WAIT_SECONDS,
            "minimum_wait_remaining_seconds": ARTIFACTS.json_number(CONFIG.MIN_PEER_WAIT_SECONDS - elapsed_seconds),
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
    cancel_reason = reason or ("minimum_wait_elapsed" if peer_alive else "peer_not_running")
    cancel_details = {
        "forced": force_before_min_wait,
        "reason": cancel_reason,
        "elapsed_seconds": ARTIFACTS.json_number(elapsed_seconds),
        "minimum_wait_seconds": CONFIG.MIN_PEER_WAIT_SECONDS,
        "before_minimum_wait": before_minimum_wait,
    }
    preserved = ARTIFACTS.preserved_artifact_hashes(run_dir)
    try:
        workspace_mutation = record_cancel_workspace_mutation(peer_repo_root, run_dir, request)
    except (OSError, ValueError, ARTIFACTS.ArtifactValidationError) as exc:
        raise SystemExit(
            f"refusing to release the workspace guard because mutation verification failed: {exc}"
        ) from exc
    completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cancel_result = {
        "schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION,
        "run_id": request["run_id"],
        "status": "cancelled",
        "outcome": "cancelled_quiescent",
        **cancel_details,
        "peer_was_alive": peer_alive,
        "process_cleanup": cleanup,
        "preserved_artifacts": preserved,
        "workspace_mutation": (
            str(run_dir / "cancel-workspace-mutation.json")
            if workspace_mutation is not None
            else None
        ),
        "workspace_mutation_sha256": ARTIFACTS.workspace_mutation_sha256(workspace_mutation),
        "completed_at": completed_at,
    }
    try:
        ARTIFACTS.validate_cancel_result(cancel_result, request, run_dir)
    except ARTIFACTS.ArtifactValidationError as exc:
        raise SystemExit(f"refusing to persist an invalid cancel result: {exc}") from exc
    if not write_json_once(cancel_result_path, cancel_result):
        existing = ARTIFACTS.load_json(cancel_result_path)
        try:
            ARTIFACTS.validate_cancel_result(existing, request, run_dir)
        except ARTIFACTS.ArtifactValidationError as exc:
            raise SystemExit(f"concurrent cancellation wrote an invalid result: {exc}") from exc
        if existing != cancel_result:
            raise SystemExit(
                "a concurrent cancellation committed different immutable evidence; manual inspection is required"
            )
    state.upsert_job(
        run_root,
        {
            **job_patch_from_run(run_dir, "cancelled", "cancelled"),
            "pid": None,
            "completed_at": completed_at,
            "cancel": cancel_details,
            "cancel_result": str(cancel_result_path),
        },
    )
    guard_path = REGISTRY.release_workspace_guard(peer_repo_root, request["run_id"], owner=process_info)
    output = {
        "schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION,
        "run_id": request["run_id"],
        "run_dir": str(run_dir),
        "pid": pid,
        "outcome": "cancelled_quiescent",
        "forced": force_before_min_wait,
        "reason": cancel_reason,
        "elapsed_seconds": ARTIFACTS.json_number(elapsed_seconds),
        "minimum_wait_seconds": CONFIG.MIN_PEER_WAIT_SECONDS,
        "cancel_result": str(cancel_result_path),
        "workspace_mutation": cancel_result["workspace_mutation"],
        "workspace_guard_released": str(guard_path),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def reset_settings(args: argparse.Namespace, repo_root: Path) -> int:
    scopes = ["local", "global"] if args.reset == "all" else [args.reset]
    removed: list[str] = []
    missing: list[str] = []
    for scope in scopes:
        path = CONFIG.settings_path(scope, repo_root)
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
    repo_root = CONFIG.canonical_repo_root(args.repo_root or CONFIG.default_repo_root())
    if args.reset:
        return reset_settings(args, repo_root)

    resolved = CONFIG.resolve_settings(repo_root, env={})
    CONFIG.require_valid_settings(resolved)
    settings = dict(resolved["settings"])
    updates = CONFIG.settings_from_args(args)
    settings.update(updates)
    should_clear_history = bool(getattr(args, "clear_history", False))
    if not args.no_input:
        try:
            settings = CONFIG.interactive_setup(settings)
            should_clear_history = should_clear_history or CONFIG.choose_bool("Clear old run history now", False)
        except EOFError:
            print("setup requires interactive stdin; rerun with --no-input for non-interactive use", file=sys.stderr)
            return 2
    if should_clear_history and args.no_input and not args.dry_run and not getattr(args, "yes", False):
        print("setup --clear-history requires --yes for deletion; rerun with --dry-run to preview", file=sys.stderr)
        return 2
    settings = CONFIG.normalize_settings(settings, "setup")
    path = CONFIG.settings_path(args.scope, repo_root)
    if not args.dry_run:
        CONFIG.write_settings_file(path, settings)
    history_cleanup = None
    if should_clear_history:
        cleanup_args = argparse.Namespace(
            repo_root=repo_root,
            run_root=CONFIG.default_run_root(repo_root),
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
        output["env"] = CONFIG.settings_to_env(settings)
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def run_command_capture(command: list[str], timeout: float = 20) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            CONFIG.SAFETY.native_command(command),
            text=True,
            encoding="utf-8",
            errors="replace",
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
            data = CONFIG.STRICT_JSON.loads(result["stdout"], max_bytes=16 * 1024 * 1024)
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
    repo_root = CONFIG.canonical_repo_root(args.repo_root or CONFIG.default_repo_root())
    run_root = (args.run_root or CONFIG.default_run_root(repo_root)).resolve()
    peer_runtime = CONFIG.load_peer_runtime()
    resolved = CONFIG.resolve_settings(repo_root)
    settings = resolved["settings"]
    cli_paths: dict[str, str | None] = {}
    cli_errors: dict[str, str] = {}
    for name in ("codex", "claude"):
        try:
            cli_paths[name] = CONFIG.SAFETY.resolve_cli(name)
        except FileNotFoundError as exc:
            cli_paths[name] = None
            cli_errors[name] = str(exc)
    codex_path = cli_paths["codex"]
    claude_path = cli_paths["claude"]
    codex_auth = run_command_capture(["codex", "login", "status"], timeout=10) if codex_path else {}
    claude_auth = parse_json_command(["claude", "auth", "status", "--json"], timeout=10) if claude_path else {}
    claude_report = claude_auth.get("json") if isinstance(claude_auth, dict) else None
    settings_errors = CONFIG.settings_error_messages(resolved)
    state_runtime = CONFIG.load_state_runtime()
    try:
        state_data = state_runtime.load_state(run_root)
    except (OSError, ValueError) as exc:
        state_check = {
            "ok": False,
            "value": str(run_root / "state.json"),
            "error": str(exc),
            "recovery": (
                "Archive this file for evidence and remove or rename it, or choose a fresh "
                "--run-root; legacy state is not migrated or coerced."
            ),
        }
    else:
        state_check = {
            "ok": True,
            "value": str(run_root / "state.json"),
            "jobs": len(state_data["jobs"]),
        }

    schema_errors: list[str] = []
    schema_paths = sorted((CONFIG.resource_root() / "schemas").glob("*.json"))
    try:
        import jsonschema
    except ImportError:
        jsonschema = None
    for schema_path in schema_paths:
        try:
            schema_data = CONFIG.STRICT_JSON.load(schema_path, max_bytes=4 * 1024 * 1024)
            if not isinstance(schema_data, dict):
                raise ValueError("schema root must be an object")
            if jsonschema is not None:
                jsonschema.Draft202012Validator.check_schema(schema_data)
        except Exception as exc:
            schema_errors.append(f"{schema_path.name}: {exc}")

    def cli_version_check(name: str, path: str | None) -> dict[str, Any]:
        minimum = CONFIG.MIN_CLI_VERSIONS[name]
        result = run_command_capture([name, "--version"], timeout=10) if path else {}
        raw = f"{result.get('stdout', '')}\n{result.get('stderr', '')}".strip()
        parsed = CONFIG.parse_cli_version(raw)
        check = {
            "ok": path is not None and parsed is not None and parsed >= minimum,
            "value": {
                "path": path,
                "version": ".".join(str(part) for part in parsed) if parsed else None,
                "minimum": ".".join(str(part) for part in minimum),
            },
        }
        if name in cli_errors:
            check["error"] = cli_errors[name]
        return check

    doctor_workspace_mode = "direct" if settings["safe_mode"] else settings["workspace_mode"]
    isolation_check = (
        CONFIG.load_isolation_runtime().preflight(repo_root)
        if doctor_workspace_mode == "isolated" else
        {"status": "not_required", "backend": None, "details": "direct workspace mode"}
    )

    checks = {
        "repo_root": {"ok": repo_root.exists(), "value": str(repo_root)},
        "run_root": {"ok": run_root.exists() or path_is_creatable(run_root), "value": str(run_root)},
        "python": {"ok": sys.version_info >= (3, 11), "value": sys.version.split()[0]},
        "platform": {
            "ok": CONFIG.is_supported_platform(),
            "value": {"os_name": os.name, "platform": sys.platform, "supported": ["Linux", "macOS", "Windows"]},
        },
        # Git is needed only inside a Git repository.
        "git": {
            "ok": shutil.which("git") is not None or not CONFIG.load_snapshot_runtime().has_git_metadata(repo_root),
            "value": shutil.which("git"),
        },
        "workspace_isolation": {
            "ok": isolation_check["status"] in {"available", "not_required"},
            "value": {"mode": doctor_workspace_mode, **isolation_check},
        },
        "claude_cli": cli_version_check("claude", claude_path),
        "codex_cli": cli_version_check("codex", codex_path),
        "settings": {
            "ok": not settings_errors,
            "value": {"local": resolved["layers"]["local"]["path"], "global": resolved["layers"]["global"]["path"]},
            "errors": settings_errors,
        },
        "schemas": {
            "ok": bool(schema_paths) and not schema_errors,
            "value": [str(path) for path in schema_paths],
            "errors": schema_errors,
            "meta_validator": "Draft202012Validator" if jsonschema is not None else "strict JSON only",
        },
        "timeout_floor_seconds": {"ok": peer_runtime.MIN_AGENT_TIMEOUT_SECONDS == 60, "value": peer_runtime.MIN_AGENT_TIMEOUT_SECONDS},
        "state_file": state_check,
        "effective_configuration": {
            "ok": not settings_errors,
            "value": {
                "codex": {"model": settings["codex_model"], "effort": settings["codex_effort"]},
                "claude": {"model": settings["claude_model"], "effort": settings["claude_effort"]},
                "online_research": settings["online_research"],
                "workspace_mode": doctor_workspace_mode,
                "uses_built_in_model_effort_defaults": (
                    all(settings[key] == CONFIG.SETTING_DEFAULTS[key] for key in (
                        "codex_model", "codex_effort", "claude_model", "claude_effort"
                    ))
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
                "codex": ["exec --strict-config", '-c approval_policy="never"', "--output-schema", "--output-last-message"],
                "claude": [
                    "--permission-mode",
                    "--tools",
                    "--disallowedTools",
                    "--json-schema",
                    "--output-format json",
                    "optional per-run --max-turns",
                ],
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
    start_parser.add_argument("--host", choices=sorted(CONFIG.ORIGINS), required=True)
    start_parser.add_argument("--mode", choices=sorted(CONFIG.MODES))
    start_parser.add_argument("--target", required=True)
    start_parser.add_argument("--brief")
    start_parser.add_argument("--brief-file", type=Path)
    start_parser.add_argument("--edit-allowed", action="store_true")
    start_parser.add_argument(
        "--adjudicator",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override the host-local advisory adjudicator for this run.",
    )
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
    start_parser.add_argument("--max-local-subagents", type=CONFIG.cli_integer)
    start_parser.add_argument(
        "--local-subagents",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override native local helper availability for this run.",
    )
    start_parser.add_argument(
        "--safe-mode",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override the resolved permission posture for this run.",
    )
    start_parser.add_argument(
        "--workspace-mode", choices=["isolated", "direct"],
        help="Select a disposable copy-on-write workspace or direct host access for this run.",
    )
    start_parser.add_argument(
        "--timeout-seconds",
        type=CONFIG.cli_number,
        default=None,
        help="Override the hard finite peer deadline for this run (60-86400 seconds).",
    )
    start_parser.add_argument(
        "--claude-max-turns",
        type=CONFIG.cli_integer,
        help="Explicitly opt into a positive Claude CLI turn cap for this run.",
    )
    start_parser.add_argument("--run-id")
    start_parser.add_argument("--run-root", type=Path)
    start_parser.add_argument("--repo-root", type=Path)
    start_parser.set_defaults(func=start)

    availability_parser = sub.add_parser(
        "check-availability",
        help="Check one exact peer model/effort pair and print a strict JSON result.",
    )
    availability_parser.add_argument("--peer", choices=sorted(CONFIG.ORIGINS), required=True)
    availability_parser.add_argument("--model", required=True)
    availability_parser.add_argument("--effort", required=True)
    availability_parser.add_argument(
        "--timeout-seconds",
        type=CONFIG.cli_number,
        default=CONFIG.DEFAULT_AVAILABILITY_TIMEOUT_SECONDS,
        help=f"Bound the provider metadata check to at most {CONFIG.MAX_AVAILABILITY_TIMEOUT_SECONDS:g} seconds.",
    )
    availability_parser.add_argument("--repo-root", type=Path)
    availability_parser.set_defaults(func=check_availability)

    setup_parser = sub.add_parser("setup", help="Configure Agent Collab local or global peer defaults.")
    setup_parser.add_argument("--scope", choices=["local", "global"], default="local")
    setup_parser.add_argument("--no-input", action="store_true", help="Do not prompt; write provided flags over current defaults.")
    setup_parser.add_argument("--dry-run", action="store_true", help="Show changes without writing or removing settings files.")
    setup_parser.add_argument("--print-env", action="store_true", help="Include equivalent environment variables in output.")
    setup_parser.add_argument("--reset", choices=sorted(CONFIG.RESET_SCOPES), help="Remove local, global, or all Agent Collab settings.")
    setup_parser.add_argument("--local-subagents-allowed", action=argparse.BooleanOptionalAction)
    setup_parser.add_argument("--max-local-subagents", type=CONFIG.cli_integer)
    setup_parser.add_argument("--adjudicator-enabled", action=argparse.BooleanOptionalAction)
    setup_parser.add_argument("--timeout-seconds", type=CONFIG.cli_number)
    setup_parser.add_argument("--history-retained-runs", type=CONFIG.cli_integer)
    setup_parser.add_argument("--clear-history", action="store_true", help="Clear old run history after setup.")
    setup_parser.add_argument("--yes", action="store_true", help="Confirm setup actions that delete run history.")
    setup_parser.add_argument("--safe-mode", action=argparse.BooleanOptionalAction)
    setup_parser.add_argument("--workspace-mode", choices=["isolated", "direct"])
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
    setup_parser.add_argument("--repo-root", type=Path)
    setup_parser.set_defaults(func=setup)

    finish_parser = sub.add_parser("finish", help="Validate peer output and write synthesis support artifacts.")
    finish_parser.add_argument("run", help="Run ID, unique prefix, or run directory.")
    finish_parser.add_argument("--first-pass", type=Path, help="Semantic JSON with summary and claims.")
    finish_parser.add_argument("--helpers", type=Path, help="Semantic JSON with a reports array.")
    finish_parser.add_argument("--adjudicator", type=Path, help="Semantic JSON adjudicator judgments without run metadata.")
    finish_parser.add_argument("--timeout-seconds", type=CONFIG.cli_number, default=None)
    finish_parser.add_argument("--run-root", type=Path)
    finish_parser.add_argument("--repo-root", type=Path)
    finish_parser.set_defaults(func=finish)

    complete_parser = sub.add_parser("complete", help="Close a synthesized run and release the run guard.")
    complete_parser.add_argument("run", help="Run ID, unique prefix, or run directory.")
    complete_parser.add_argument("--synthesis", type=Path, help="Edited synthesis.json generated by finish; metadata is filled automatically.")
    complete_parser.add_argument("--run-root", type=Path)
    complete_parser.add_argument("--repo-root", type=Path)
    complete_parser.set_defaults(func=complete)

    status_parser = sub.add_parser("status", help="Print active/recent jobs or one run's artifact status.")
    status_parser.add_argument("run", nargs="?", help="Optional run ID, unique prefix, or run directory.")
    status_parser.add_argument("--active", action="store_true", help="List active worktree registry owners across run roots.")
    status_parser.add_argument("--all", action="store_true", help="List all retained jobs instead of the newest eight.")
    status_parser.add_argument("--wait", action="store_true", help="When inspecting one run, wait while the peer is alive.")
    status_parser.add_argument("--timeout-seconds", type=CONFIG.cli_number, default=2700)
    status_parser.add_argument("--poll-interval-seconds", type=CONFIG.cli_number, default=2)
    status_parser.add_argument("--run-root", type=Path)
    status_parser.add_argument("--repo-root", type=Path)
    status_parser.set_defaults(func=status)

    result_parser = sub.add_parser("result", help="Print complete stored output for a run.")
    result_parser.add_argument("run", help="Explicit run ID or run directory.")
    result_parser.add_argument("--format", choices=["json", "markdown"], default="json")
    result_parser.add_argument("--review", type=Path, help="Evidence revision directory returned by finish.")
    result_parser.add_argument("--run-root", type=Path)
    result_parser.add_argument("--repo-root", type=Path)
    result_parser.set_defaults(func=result)

    clear_history_parser = sub.add_parser("clear-history", help="Delete old Agent Collab run history.")
    clear_history_parser.add_argument("--retain", type=CONFIG.cli_integer, help="Number of newest terminal runs to keep.")
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

    recover_parser = sub.add_parser("recover", help="Recover one abandoned starting registry entry without deleting its artifacts.")
    recover_parser.add_argument("run", help="Exact active run ID.")
    recover_parser.add_argument("--repo-root", type=Path)
    recover_parser.set_defaults(func=recover)

    doctor_parser = sub.add_parser("doctor", help="Check Agent Collab local runtime prerequisites.")
    doctor_parser.add_argument("--run-root", type=Path)
    doctor_parser.add_argument("--repo-root", type=Path)
    doctor_parser.set_defaults(func=doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    CONFIG.STRICT_JSON.use_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"Agent Collab: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
