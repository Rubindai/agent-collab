"""Run resolution, validated inputs and read-only status projections."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
from typing import Any
import host_config as CONFIG
import artifacts as ARTIFACTS
import registry as REGISTRY
import process_tree as PROCESS_TREE


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



def emit_result(result: tuple[int, dict[str, Any]]) -> int:
    code, output = result
    if "next_action" in output:
        output = {"next_action": output["next_action"], **output}
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys="next_action" not in output))
    return code



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



def require_run_request(run_dir: Path) -> dict[str, Any]:
    request = ARTIFACTS.load_json(run_dir / "host-request.json")
    ARTIFACTS.require_artifact_version(request, "host-request")
    try:
        CONFIG.PEER.validate_request(request)
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
        state = CONFIG.STATE
        if CONFIG.is_safe_run_id(reference):
            job = state.find_job(root, reference)
            if job and job.get("run_dir"):
                return ensure_run_dir(Path(str(job["run_dir"])).expanduser(), root)
        raise SystemExit(f"unknown Agent Collab run: {reference}")

    state = CONFIG.STATE
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
        PROCESS_TREE.process_identity_matches(process_info) and PROCESS_TREE.process_alive(int(pid))
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
    elapsed_seconds = PROCESS_TREE.peer_elapsed_seconds(process_info, run_dir)
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



def is_direct_run_dir(run_root: Path, run_dir: Path) -> bool:
    try:
        resolved_root = run_root.resolve()
        resolved_run = run_dir.resolve()
    except FileNotFoundError:
        return False
    return resolved_run.parent == resolved_root and any((resolved_run / name).exists() for name in RUN_ARTIFACT_NAMES)

