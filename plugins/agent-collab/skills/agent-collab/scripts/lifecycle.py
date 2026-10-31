"""Run finalization, evidence admission, completion and cancellation."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import host_config as CONFIG
import artifacts as ARTIFACTS
import registry as REGISTRY
import runs as RUNS
import process_tree as PROCESS_TREE


FINISH_WAIT_POLL_SECONDS = 1



def next_action(run_dir: Path, repo_root: Path, stage: str, synthesis_input: str | None = None) -> dict[str, Any]:
    """Return executable argv and only the instructions needed at this stage."""
    command = [sys.executable, str(CONFIG.runtime_dir() / "host.py"), "advance", str(run_dir), "--repo-root", str(repo_root)]
    actions = {
        "first_pass": ("--first-pass", str(run_dir / "first-pass.input.json"),
            "Complete independent host analysis without reading peer output or polling status. Save summary and claims, then submit this file.",
            {"summary": "", "claims": []}),
        "adjudicator": ("--adjudicator", str(run_dir / "adjudicator.input.json"),
            "Read evidence_file, then obtain a fresh host-local advisory report using the neutral brief and that evidence; follow the skill's helper rules. If no advisory helper is available, document that limitation in synthesis and submit it with --synthesis instead.",
            {"status": "ok", "summary": "", "false_positives": [], "claims_needing_verification": [],
             "recommended_verdict": "", "claims": []}),
        "synthesis": ("--synthesis", synthesis_input,
            "Read evidence_file completely and verify material claims. Edit the generated synthesis input, then submit it immediately before answering. Changed evidence requires a newly reviewed revision.", None),
        "waiting": (None, None, "Peer remains within its finite deadline. Resume advance; do not replace or cancel it unless the user stops the run.", None),
    }
    flag, path, instruction, template = actions[stage]
    if flag:
        command.extend([flag, path])
    result = {"stage": stage, "command": command, "instruction": instruction}
    if path:
        result["input_path"] = path
    if template is not None:
        result["input_template"] = template
    return result



def host_policy(request: dict[str, Any]) -> dict[str, Any]:
    return {key: request[key] for key in ("adjudicator_enabled", "local_subagents_allowed",
        "max_local_subagents", "online_research", "edit_allowed", "workspace_mode", "peer_timeout_seconds")}



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
    snapshot = CONFIG.SNAPSHOT
    deadline = time.monotonic() + snapshot.DEFAULT_SNAPSHOT_TIMEOUT_SECONDS
    data = (
        {"mode": "isolated", "digest": hashlib.sha256(str(repo_root).encode()).hexdigest()}
        if workspace_mode == "isolated"
        else snapshot.mutation_snapshot(repo_root, ignored_paths=ignored_paths, deadline=deadline)
    )
    CONFIG.STRICT_JSON.write(ARTIFACTS.snapshot_json_path(output_path), data)
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
    snapshot = CONFIG.SNAPSHOT
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
            CONFIG.STRICT_JSON.write(mutation_path, diagnostic)
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



def resolve_finish_wait_timeout(
    explicit_timeout_seconds: float | None,
    process_info: dict[str, Any],
    *, guided: bool = False,
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
            return (float(CONFIG.MIN_PEER_WAIT_SECONDS), "guided_default") if guided else (derived, "peer_timeout_plus_grace")
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
            alive = PROCESS_TREE.process_identity_matches(process_info) and PROCESS_TREE.process_alive(pid)
            if not alive:
                return "report_ready"
            now = time.time()
            if now >= deadline:
                return "timeout"
            # Report bytes are not terminal while the wrapper is alive. The
            # wrapper owns provider-group cleanup and may still replace them;
            # process exit is the readiness boundary.
        else:
            alive = PROCESS_TREE.process_identity_matches(process_info) and PROCESS_TREE.process_alive(pid)
            now = time.time()
            if now >= deadline:
                return "timeout"
        if not alive:
            return "peer_exited"
        time.sleep(FINISH_WAIT_POLL_SECONDS)



def review_documents(run_dir: Path) -> dict[str, Any]:
    return {name: ARTIFACTS.read_json_if_exists(run_dir / name) for name in CONFIG.REVIEW.EVIDENCE_FILES}



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
        review = CONFIG.REVIEW
        admission = review.verify_admissions(run_dir, process_info, replacing={item[1] for item in supplied})
        for option, filename, allowed, validate in supplied:
            if option == "first_pass" and admission["reports"][filename] is None and (
                    (run_dir / filename).exists() or (run_dir / "reviews").exists()):
                raise ARTIFACTS.ArtifactValidationError("cannot re-admit an existing first pass without its original receipt")
            value = ARTIFACTS.load_json(getattr(args, option))
            required = allowed - ({"claims"} if option == "adjudicator" else set())
            if not required <= set(value) <= allowed:
                raise ARTIFACTS.ArtifactValidationError(f"{option} input keys must be {sorted(allowed)}; only adjudicator claims are optional")
            if option == "adjudicator" and (not request["adjudicator_enabled"] or value.get("status") == "advisory_pending"):
                raise ARTIFACTS.ArtifactValidationError("adjudicator input requires enabled adjudication and an actual report, not advisory_pending")
            artifact = {"schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION, "run_id": request["run_id"], **value}
            validate(artifact, request)
            pending.append((run_dir / filename, artifact))
        merged = {path.name: artifact for path, artifact in pending}
        for name in ("helper-reports.json", "adjudicator-report.json"):
            if name not in merged:
                merged[name] = ARTIFACTS.read_json_if_exists(run_dir / name)
        ARTIFACTS.validate_host_helper_budget(merged["helper-reports.json"], merged["adjudicator-report.json"], request)
        for path, artifact in pending:
            digest = review.digest(artifact)
            if path.name == "host-first-pass.json" and admission["reports"][path.name] not in {None, digest}:
                raise ARTIFACTS.ArtifactValidationError("the admitted independent first pass is immutable; put later judgments in synthesis")
            admission["reports"][path.name] = digest
        for path, artifact in pending:
            CONFIG.STRICT_JSON.write(path, artifact)
        CONFIG.STRICT_JSON.write(run_dir / "host-inputs.json", admission)



def finish(args: argparse.Namespace) -> int:
    return RUNS.emit_result(finish_run(args))



def finish_run(args: argparse.Namespace, *, guided: bool = False) -> tuple[int, dict[str, Any]]:
    _, run_root, run_dir = RUNS.resolve_run_context(args)
    state = CONFIG.STATE
    request = RUNS.require_run_request(run_dir)
    process_info = ARTIFACTS.load_json(run_dir / "peer-process.json")
    ARTIFACTS.validate_peer_process(process_info, request, run_dir)
    ingest_finish_inputs(args, run_dir, request, process_info)
    CONFIG.REVIEW.verify_admissions(run_dir, process_info)
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
    configured_wait_seconds, finish_timeout_source = resolve_finish_wait_timeout(args.timeout_seconds, process_info, guided=guided)
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
            guided=guided,
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
    guided: bool = False,
) -> tuple[int, dict[str, Any]]:
    current_request = RUNS.require_run_request(run_dir)
    if current_request != request:
        raise SystemExit("host-request changed while finish was waiting; refusing finalization")
    current_process = ARTIFACTS.load_json(run_dir / "peer-process.json")
    if current_process != process_info:
        raise SystemExit("peer-process changed while finish was waiting; refusing finalization")
    CONFIG.REVIEW.verify_admissions(run_dir, process_info)
    current_host_report = ARTIFACTS.load_json(run_dir / "host-first-pass.json")
    if current_host_report != host_report:
        raise SystemExit("host-first-pass changed while waiting; resume advance")
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
                **RUNS.job_patch_from_run(run_dir, "running", "waiting_for_peer"),
                "pid": pid,
                "finish_wait_seconds": ARTIFACTS.json_number(finish_wait_seconds),
                "finish_timeout_source": finish_timeout_source,
            },
        )
        CONFIG.STRICT_JSON.write(run_dir / "host-result.json", output)
        if guided:
            output["next_action"] = next_action(run_dir, repo_root, "waiting")
        return (0 if guided else 1), output

    process_tree_proven = False
    if time.time() >= hard_deadline:
        try:
            PROCESS_TREE.require_process_tree_quiescent(
                process_info,
                run_dir,
                request,
                wrapper_quiescence_proven=wrapper_quiescence_proven,
            )
        except ARTIFACTS.ArtifactValidationError:
            cleanup = PROCESS_TREE.terminate_tracked_process_tree(process_info, run_dir, request)
            if cleanup["quiescent"] is not True:
                raise SystemExit(
                    "hard peer deadline elapsed but the tracked process tree could not be proven quiescent; "
                    "the workspace guard remains active:\n"
                    + json.dumps(cleanup, ensure_ascii=False, indent=2, sort_keys=True)
                )
            peer_runtime = CONFIG.PEER
            CONFIG.STRICT_JSON.write(
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
            PROCESS_TREE.require_process_tree_quiescent(
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
        failure = CONFIG.PEER.failure("peer_no_output", "Peer report missing or empty.", request)
        failure["summary"] = "Peer process produced no report before finish timeout."
        CONFIG.STRICT_JSON.write(peer_report_path, failure)

    peer_runtime = CONFIG.PEER
    peer_report, validation_status = ARTIFACTS.normalize_peer_report_from_artifacts(peer_runtime, run_dir, request)

    adjudicator_path = run_dir / "adjudicator-report.json"
    adjudicator: dict[str, Any] | None = None
    if request["adjudicator_enabled"]:
        if adjudicator_path.exists():
            adjudicator = ARTIFACTS.load_json(adjudicator_path)
        else:
            adjudicator = ARTIFACTS.default_adjudicator_report(request, peer_report)
            CONFIG.STRICT_JSON.write(adjudicator_path, adjudicator)
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
        ARTIFACTS.validate_host_helper_budget(ARTIFACTS.read_json_if_exists(run_dir / "helper-reports.json"), adjudicator, request)
        claim_matrix = ARTIFACTS.build_claim_matrix(
            request, host_report, peer_report, helper_claims, adjudicator, helper_digest
        )
    except ARTIFACTS.ArtifactValidationError as exc:
        raise SystemExit(f"invalid helper or claim-matrix artifact in {run_dir}: {exc}") from exc
    CONFIG.STRICT_JSON.write(run_dir / "claim-matrix.json", claim_matrix)

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
            **RUNS.job_patch_from_run(run_dir, "synthesizing", "ready_for_synthesis"),
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
    CONFIG.STRICT_JSON.write(run_dir / "host-result.json", ready_result)
    review = CONFIG.REVIEW
    documents = review_documents(run_dir)
    review_paths = review.prepare(run_dir, process_info, documents)
    if guided:
        helper_count = len((documents["helper-reports.json"] or {}).get("reports", []))
        advisory_available = request["local_subagents_allowed"] and helper_count < request["max_local_subagents"]
        stage = "adjudicator" if adjudicator and adjudicator["status"] == "advisory_pending" and advisory_available else "synthesis"
        return 0, {"run_dir": str(run_dir), "peer_status": peer_report["status"],
                   "validation_status": validation_status, "host_policy": host_policy(request), **review_paths,
                   "next_action": next_action(run_dir, repo_root, stage, review_paths["synthesis_input"])}
    return 0, {**ready_result, **review_paths}



def complete(args: argparse.Namespace) -> int:
    context = RUNS.resolve_run_context(args)
    with REGISTRY.run_operation_lock(context[2]):
        return RUNS.emit_result(_complete_locked(args, context))



def _complete_locked(args: argparse.Namespace, context: tuple[Path, Path, Path]) -> tuple[int, dict[str, Any]]:
    _, run_root, run_dir = context
    request = RUNS.require_run_request(run_dir)
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
        raise SystemExit("run is not ready to complete; resume advance and synthesize the evidence first")
    CONFIG.REVIEW.verify_admissions(run_dir, process_info)
    if phase != "done" and getattr(args, "synthesis", None) is None:
        raise SystemExit("completion requires --synthesis with the revision-bound input returned by advance")
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
            PROCESS_TREE.require_process_tree_quiescent(
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
            CONFIG.STRICT_JSON.write(host_result_path, host_result)
            raise SystemExit(
                "workspace mutation evidence changed after finish; host-result.json was refreshed. "
                "Resume advance without --synthesis, inspect the new review revision, and resynthesize before completing."
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
                PROCESS_TREE.require_process_tree_quiescent(
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
        judgments = CONFIG.REVIEW.judgments(run_dir, args.synthesis, process_info, review_documents(run_dir))
        synthesis = {"schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION, "run_id": request["run_id"],
                     **judgments, "claim_matrix_sha256": host_result["claim_matrix_sha256"],
                     "workspace_mutation_sha256": host_result["workspace_mutation_sha256"], "final_answer_ready": True}
        ARTIFACTS.validate_host_synthesis(synthesis, request)
        if phase == "done":
            if ARTIFACTS.load_json(run_dir / "host-synthesis.json") != synthesis:
                raise ARTIFACTS.ArtifactValidationError("cannot rewrite a completed synthesis")
        else:
            CONFIG.STRICT_JSON.write(run_dir / "host-synthesis.json", synthesis)

    peer_runtime = CONFIG.PEER
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
    state = CONFIG.STATE
    state.upsert_job(
        run_root,
        {
            **RUNS.job_patch_from_run(run_dir, state_status, "done"),
            "peer_status": peer_report["status"],
            "peer_verdict": peer_report["verdict"],
            "completed_at": completed_at,
            "summary": peer_report["summary"],
        },
    )

    if phase == "done":
        guard_path, active = REGISTRY.read_workspace_guard(repo_root, request["run_id"])
        release_guard = active is not None
        guard_key = "workspace_guard_released" if release_guard else "workspace_guard"
    else:
        done_result = {**host_result, "phase": "done", "completed_at": completed_at}
        ARTIFACTS.validate_host_result(done_result, request, run_dir)
        CONFIG.STRICT_JSON.write(host_result_path, done_result)
        release_guard, guard_key = True, "workspace_guard_released"
    if release_guard:
        guard_path = REGISTRY.release_workspace_guard(repo_root, request["run_id"], owner=process_info)
    return 0, {
        "schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION,
        "run_id": request["run_id"],
        "run_dir": str(run_dir),
        "status": "already_complete" if phase == "done" else state_status,
        guard_key: str(guard_path),
    }



def advance(args: argparse.Namespace) -> int:
    """Resume from validated artifacts, using the same finalization and closure paths."""
    evidence_inputs = any(getattr(args, key) is not None for key in ("first_pass", "helpers", "adjudicator"))
    if args.synthesis is not None and evidence_inputs:
        raise ValueError("submit evidence and synthesis separately so the new evidence is reviewed first")
    context = RUNS.resolve_run_context(args)
    _, _, run_dir = context
    with REGISTRY.run_operation_lock(run_dir):
        request = RUNS.require_run_request(run_dir)
        process = ARTIFACTS.load_json(run_dir / "peer-process.json")
        ARTIFACTS.validate_peer_process(process, request, run_dir)
        if (run_dir / "cancel-result.json").exists():
            raise ValueError("run has cancellation evidence; use cancel RUN to validate or resume cancellation")
        current = ARTIFACTS.read_json_if_exists(run_dir / "host-result.json")
        if current is not None:
            ARTIFACTS.validate_host_result(current, request, run_dir)
        if args.synthesis is not None and (not current or current["phase"] not in {"ready_for_synthesis", "done"}):
            raise ValueError("synthesis requires returned review evidence; resume advance without --synthesis first")
        if args.synthesis is not None or (current and current["phase"] == "done"):
            if evidence_inputs:
                raise ValueError("cannot replace evidence of a terminal run")
            code, output = _complete_locked(args, context)
            return RUNS.emit_result((code, {**output, "next_action": None}))
        REGISTRY.require_workspace_guard_owner(Path(process["repo_root"]), request["run_id"], owner=process)
        admission = CONFIG.REVIEW.verify_admissions(run_dir, process)
        if args.adjudicator is not None and (not current or current["phase"] != "ready_for_synthesis"):
            raise ValueError("advisory input requires reviewed independent reports; submit the first pass with advance first")
        if admission["reports"]["host-first-pass.json"] is None and args.first_pass is None:
            if evidence_inputs:
                raise ValueError("submit the independent first pass before other host reports")
            return RUNS.emit_result((0, {"run_dir": str(run_dir), "host_policy": host_policy(request),
                                   "next_action": next_action(run_dir, Path(process["repo_root"]), "first_pass")}))
    # No run-operation lock is held while waiting for the provider. Finalization
    # rechecks ownership and evidence under the lock before publishing a revision.
    return RUNS.emit_result(finish_run(args, guided=True))



def cancel(args: argparse.Namespace) -> int:
    context = RUNS.resolve_run_context(args)
    with REGISTRY.run_operation_lock(context[2]):
        return _cancel_locked(args, context)



def cancellation_job_patch(run_dir: Path, result: dict[str, Any]) -> dict[str, Any]:
    """Project persisted cancellation evidence without moving lifecycle operations."""
    return {
        **RUNS.job_patch_from_run(run_dir, "cancelled", "cancelled"),
        "pid": None,
        "completed_at": result["completed_at"],
        "cancel": {key: result[key] for key in (
            "forced", "reason", "elapsed_seconds", "minimum_wait_seconds", "before_minimum_wait",
        )},
        "cancel_result": str(run_dir / "cancel-result.json"),
    }



def _cancel_locked(args: argparse.Namespace, context: tuple[Path, Path, Path]) -> int:
    repo_root, run_root, run_dir = context
    process_info = ARTIFACTS.load_json(run_dir / "peer-process.json")
    request = RUNS.require_run_request(run_dir)
    try:
        ARTIFACTS.validate_peer_process(process_info, request, run_dir)
    except ARTIFACTS.ArtifactValidationError as exc:
        raise SystemExit(f"invalid peer-process artifact in {run_dir}: {exc}") from exc
    peer_repo_root = Path(process_info["repo_root"]).resolve()
    cancel_result_path = run_dir / "cancel-result.json"
    state = CONFIG.STATE

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
                PROCESS_TREE.require_process_tree_quiescent(
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
        state.upsert_job(run_root, cancellation_job_patch(run_dir, existing))
        output = {
            "schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION,
            "run_id": request["run_id"],
            "run_dir": str(run_dir),
            "outcome": "already_cancelled",
            "cancel_result": str(cancel_result_path),
            "workspace_guard": str(guard_path),
            "workspace_guard_released": released,
        }
        return RUNS.emit_result((0, output))

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

    identity_matches = PROCESS_TREE.process_identity_matches(process_info)
    peer_alive = PROCESS_TREE.process_alive(pid) if identity_matches else False
    elapsed_seconds = PROCESS_TREE.peer_elapsed_seconds(process_info, run_dir)
    before_minimum_wait = (
        peer_alive
        and elapsed_seconds is not None
        and elapsed_seconds < CONFIG.MIN_PEER_WAIT_SECONDS
    )

    try:
        PROCESS_TREE.require_process_tree_quiescent(
            process_info,
            run_dir,
            request,
            wrapper_quiescence_proven=wrapper_quiescence_proven,
        )
    except ARTIFACTS.ArtifactValidationError:
        pass
    else:
        peer_runtime = CONFIG.PEER
        if ARTIFACTS.valid_terminal_peer_report(peer_runtime, run_dir, request):
            output = {
                "schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION,
                "run_id": request["run_id"],
                "run_dir": str(run_dir),
                "pid": pid,
                "outcome": "already_terminal",
                "message": (
                    "A valid terminal peer report already exists. The workspace guard was retained; "
                    "resume advance and synthesize so the evidence is not converted into a cancellation."
                ),
            }
            return RUNS.emit_result((1, output))

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
        return RUNS.emit_result((1, output))

    cleanup = PROCESS_TREE.terminate_tracked_process_tree(process_info, run_dir, request)
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
    state.upsert_job(run_root, cancellation_job_patch(run_dir, cancel_result))
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
    return RUNS.emit_result((0, output))

