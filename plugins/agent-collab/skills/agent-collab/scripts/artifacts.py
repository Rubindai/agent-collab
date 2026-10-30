"""Strict run artifact validation and evidence assembly."""
from __future__ import annotations
import hashlib
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import host_config as CONFIG

MAX_JSON_ARTIFACT_BYTES = 16 * 1024 * 1024


COVERAGE_LIMITATIONS = [
    "Known high-volume generated and dependency directories are excluded.",
    "Large manifests retain complete digests but only a bounded path sample.",
    "Transient changes that were reverted before a snapshot are not observable.",
]


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


VALIDATION_STATUSES = {
    "ok",
    "invalid_json",
    "noncanonical_output",
    "schema_validation_failed",
    "peer_report_mismatch",
}


FINISH_TIMEOUT_SOURCES = {"explicit", "peer_timeout_plus_grace"}


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


def snapshot_json_path(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.name}.json")


def canonical_artifact_sha256(value: dict[str, Any] | None) -> str:
    canonical = CONFIG.STRICT_JSON.dumps(value, indent=None, sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def workspace_mutation_sha256(value: dict[str, Any] | None) -> str:
    """Bind synthesis to the exact mutation evidence visible at finish."""

    return canonical_artifact_sha256(value)


def claim_matrix_sha256(value: dict[str, Any]) -> str:
    """Bind the final synthesis to the admitted claims, including helper claims."""

    return canonical_artifact_sha256(value)


def file_sha256_or_none(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def validate_cancel_mutation_evidence(
    value: dict[str, Any], request: dict[str, Any], run_dir: Path
) -> None:
    """Validate a committed cancellation snapshot without taking a newer one.

    Snapshot file hashes are streamed: an after snapshot may grow beyond the
    baseline admission limit and must not make cancellation unrecoverable.
    """

    required = {
        "changed", "snapshot_mode", "before_digest", "after_digest",
        "changed_path_count", "changed_paths", "changed_paths_truncated",
        "schema_version", "source", "edit_allowed", "message",
        "coverage_limitations", "before_snapshot_sha256", "after_snapshot_sha256",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ArtifactValidationError("invalid immutable cancellation mutation evidence fields")
    if (
        value["schema_version"] != CONFIG.ARTIFACT_SCHEMA_VERSION
        or value["source"] != "cancel_snapshot"
        or value["changed"] is not True
        or type(value["edit_allowed"]) is not bool
        or value["edit_allowed"] != request["edit_allowed"]
        or value["snapshot_mode"] not in {"git", "filesystem", "isolated"}
        or value["message"] != "Workspace changed during the Agent Collab run; attribution is unknown."
        or value["coverage_limitations"] != COVERAGE_LIMITATIONS
    ):
        raise ArtifactValidationError("invalid immutable cancellation mutation evidence metadata")
    paths, count = value["changed_paths"], value["changed_path_count"]
    if (
        not isinstance(paths, list)
        or not all(isinstance(path, str) for path in paths)
        or type(count) is not int or count < 0
        or len(paths) != min(count, 200)
        or type(value["changed_paths_truncated"]) is not bool
        or value["changed_paths_truncated"] != (
            count > 200 or any("beyond retained" in path for path in paths)
        )
    ):
        raise ArtifactValidationError("invalid immutable cancellation changed-path evidence")
    for key in ("before_digest", "after_digest", "before_snapshot_sha256", "after_snapshot_sha256"):
        if not isinstance(value[key], str) or re.fullmatch(r"[0-9a-f]{64}", value[key]) is None:
            raise ArtifactValidationError(f"invalid cancellation {key}")
    for prefix, name in (("before", "before.snapshot"), ("after", "cancel-after.snapshot")):
        snapshot_path = snapshot_json_path(run_dir / name)
        if value[f"{prefix}_snapshot_sha256"] != file_sha256_or_none(snapshot_path):
            raise ArtifactValidationError(f"recorded cancellation {prefix} snapshot changed or is missing")
    before = load_json(snapshot_json_path(run_dir / "before.snapshot"))
    if value["before_digest"] != before.get("digest"):
        raise ArtifactValidationError("cancellation baseline digest does not match the admitted snapshot")


CANCEL_PRESERVED_ARTIFACTS = {
    "peer_report": "peer-report.json",
    "peer_raw": "peer.raw.json",
    "peer_normalization": "peer-normalization.json",
    "host_result": "host-result.json",
    "host_synthesis": "host-synthesis.json",
    "claim_matrix": "claim-matrix.json",
}


def preserved_artifact_hashes(run_dir: Path) -> dict[str, str | None]:
    return {
        key: file_sha256_or_none(run_dir / filename)
        for key, filename in CANCEL_PRESERVED_ARTIFACTS.items()
    }


def validate_cleanup_evidence(value: Any, label: str) -> None:
    required = {"outcome", "quiescent"}
    allowed = required | {"pgid", "details"}
    if not isinstance(value, dict) or not required <= set(value) or set(value) - allowed:
        raise ArtifactValidationError(
            f"{label} must contain outcome and quiescent with only optional pgid/details"
        )
    if not isinstance(value["outcome"], str) or not value["outcome"]:
        raise ArtifactValidationError(f"{label} outcome must be a non-empty string")
    if type(value["quiescent"]) is not bool:
        raise ArtifactValidationError(f"{label} quiescent must be a JSON boolean")
    if "pgid" in value and (type(value["pgid"]) is not int or value["pgid"] <= 0):
        raise ArtifactValidationError(f"{label} pgid must be a positive integer")
    if "details" in value and (not isinstance(value["details"], str) or not value["details"]):
        raise ArtifactValidationError(f"{label} details must be a non-empty string")


def validate_cancel_result(report: dict[str, Any], request: dict[str, Any], run_dir: Path) -> None:
    required = {
        "schema_version",
        "run_id",
        "status",
        "outcome",
        "forced",
        "reason",
        "elapsed_seconds",
        "minimum_wait_seconds",
        "before_minimum_wait",
        "peer_was_alive",
        "process_cleanup",
        "preserved_artifacts",
        "workspace_mutation",
        "workspace_mutation_sha256",
        "completed_at",
    }
    if not isinstance(report, dict) or set(report) != required:
        raise ArtifactValidationError(f"cancel-result must contain exactly {sorted(required)}")
    if report["schema_version"] != CONFIG.ARTIFACT_SCHEMA_VERSION or report["run_id"] != request["run_id"]:
        raise ArtifactValidationError("cancel-result schema_version/run_id does not match the request")
    if report["status"] != "cancelled" or report["outcome"] != "cancelled_quiescent":
        raise ArtifactValidationError("cancel-result status/outcome is invalid")
    for key in ("forced", "before_minimum_wait", "peer_was_alive"):
        if type(report[key]) is not bool:
            raise ArtifactValidationError(f"cancel-result {key} must be a JSON boolean")
    if not isinstance(report["reason"], str) or not report["reason"]:
        raise ArtifactValidationError("cancel-result reason must be a non-empty string")
    elapsed = report["elapsed_seconds"]
    if elapsed is not None and (
        type(elapsed) not in {int, float} or not math.isfinite(float(elapsed)) or float(elapsed) < 0
    ):
        raise ArtifactValidationError("cancel-result elapsed_seconds must be a non-negative finite number or null")
    minimum_wait = report["minimum_wait_seconds"]
    if type(minimum_wait) not in {int, float} or float(minimum_wait) != CONFIG.MIN_PEER_WAIT_SECONDS:
        raise ArtifactValidationError("cancel-result minimum_wait_seconds is invalid")
    cleanup = report["process_cleanup"]
    if not isinstance(cleanup, dict) or set(cleanup) != {"quiescent", "provider", "wrapper"}:
        raise ArtifactValidationError("cancel-result process_cleanup keys are invalid")
    if cleanup["quiescent"] is not True:
        raise ArtifactValidationError("cancel-result must prove process-tree quiescence")
    validate_cleanup_evidence(cleanup["provider"], "cancel-result provider cleanup")
    validate_cleanup_evidence(cleanup["wrapper"], "cancel-result wrapper cleanup")
    if cleanup["provider"]["quiescent"] is not True or cleanup["wrapper"]["quiescent"] is not True:
        raise ArtifactValidationError("cancel-result provider and wrapper cleanup must be quiescent")
    preserved = report["preserved_artifacts"]
    if not isinstance(preserved, dict) or set(preserved) != set(CANCEL_PRESERVED_ARTIFACTS):
        raise ArtifactValidationError("cancel-result preserved_artifacts keys are invalid")
    for value in preserved.values():
        if value is not None and (not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None):
            raise ArtifactValidationError("cancel-result artifact hashes must be lowercase SHA-256 or null")
    mutation = report["workspace_mutation"]
    mutation_path = run_dir / "cancel-workspace-mutation.json"
    if mutation is not None:
        expected_path = mutation_path.resolve()
        if not isinstance(mutation, str) or Path(mutation).resolve() != expected_path:
            raise ArtifactValidationError("cancel-result workspace_mutation path is invalid")
        mutation_value = load_json(mutation_path)
        validate_cancel_mutation_evidence(mutation_value, request, run_dir)
    else:
        if mutation_path.exists():
            raise ArtifactValidationError(
                "cancel-result workspace_mutation is null but its diagnostic exists"
            )
        mutation_value = None
    if report["workspace_mutation_sha256"] != workspace_mutation_sha256(mutation_value):
        raise ArtifactValidationError("cancel-result workspace_mutation_sha256 is invalid")
    if parse_utc_timestamp(report["completed_at"]) is None:
        raise ArtifactValidationError("cancel-result completed_at must be an ISO-8601 timestamp")


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


def read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    return load_json(path)


def require_artifact_version(data: dict[str, Any], label: str) -> None:
    if data.get("schema_version") != CONFIG.ARTIFACT_SCHEMA_VERSION:
        raise SystemExit(
            f"{label} schema_version must be {CONFIG.ARTIFACT_SCHEMA_VERSION}; legacy runs are not supported"
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
        "adjudicator_enabled",
        "started_at",
        "started_at_epoch",
        "peer_timeout_seconds",
        "peer_cli_version",
        "workspace_guard",
        "owner_token",
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
    if data["schema_version"] != CONFIG.ARTIFACT_SCHEMA_VERSION:
        raise ArtifactValidationError("peer-process schema_version must be 2.0; legacy artifacts are not supported")
    if not isinstance(data["owner_token"], str) or re.fullmatch(r"[0-9a-f]{32}", data["owner_token"]) is None:
        raise ArtifactValidationError("peer-process owner_token must be 32 lowercase hexadecimal characters")
    for key in ("run_id", "host", "peer", "adjudicator_enabled"):
        if data[key] != request[key]:
            raise ArtifactValidationError(f"peer-process {key} does not match host request")
    if type(data["adjudicator_enabled"]) is not bool:
        raise ArtifactValidationError("peer-process adjudicator_enabled must be a JSON boolean")
    if not isinstance(data["run_dir"], str) or Path(data["run_dir"]).resolve() != run_dir.resolve():
        raise ArtifactValidationError("peer-process run_dir does not match the resolved run directory")
    for key in ("pid", "pgid"):
        if type(data[key]) is not int or data[key] <= 0:
            raise ArtifactValidationError(f"peer-process {key} must be a positive integer")
    try:
        CONFIG.SAFETY.validate_process_identity(data["process_identity"])
    except ValueError as exc:
        raise ArtifactValidationError(f"peer-process process_identity is invalid: {exc}") from exc
    if data["process_identity"]["pid"] != data["pid"] or data["process_identity"]["pgid"] != data["pgid"]:
        raise ArtifactValidationError("peer-process identity pid/pgid must match peer-process pid/pgid")
    if CONFIG.SAFETY.identity_is_foreign(data["process_identity"]):
        # Its process groups cannot be observed here, so no quiescence proof
        # can release its guard.
        raise ArtifactValidationError(
            f"run was started on another platform ({data['process_identity']['kind']}); "
            "finish, complete, or cancel it from that platform"
        )
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
        or not CONFIG.MIN_PEER_WAIT_SECONDS <= float(timeout) <= CONFIG.SAFETY.MAX_AGENT_TIMEOUT_SECONDS
    ):
        raise ArtifactValidationError("peer-process peer_timeout_seconds must be a finite bounded number")
    if float(timeout) != float(request["peer_timeout_seconds"]):
        raise ArtifactValidationError("peer-process peer_timeout_seconds does not match host request")
    started_at = parse_utc_timestamp(data["started_at"])
    if abs(started_at - float(data["started_at_epoch"])) > 2:
        raise ArtifactValidationError("peer-process started_at and started_at_epoch disagree")
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
    if data["schema_version"] != CONFIG.ARTIFACT_SCHEMA_VERSION or data["run_id"] != request["run_id"]:
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
            CONFIG.SAFETY.validate_process_identity(data["process_identity"])
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


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = CONFIG.STRICT_JSON.load(path, max_bytes=MAX_JSON_ARTIFACT_BYTES)
    except (OSError, ValueError) as exc:
        raise ArtifactValidationError(f"invalid artifact {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ArtifactValidationError(f"{path} must contain a JSON object")
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
    if report["schema_version"] != CONFIG.ARTIFACT_SCHEMA_VERSION:
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
        "claim_matrix_sha256",
        "final_answer_ready",
    }
    if not isinstance(report, dict) or set(report) != required:
        raise ArtifactValidationError(f"host-synthesis must contain exactly {sorted(required)}")
    if report["schema_version"] != CONFIG.ARTIFACT_SCHEMA_VERSION:
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
    for key in ("workspace_mutation_sha256", "claim_matrix_sha256"):
        if not isinstance(report[key], str) or re.fullmatch(r"[0-9a-f]{64}", report[key]) is None:
            raise ArtifactValidationError(f"host-synthesis {key} must be a lowercase SHA-256 digest")
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
        if report["schema_version"] != CONFIG.ARTIFACT_SCHEMA_VERSION or report["run_id"] != request["run_id"]:
            raise ArtifactValidationError("host-result schema_version/run_id does not match the v2 request")
        if not isinstance(report["run_dir"], str) or Path(report["run_dir"]).resolve() != run_dir.resolve():
            raise ArtifactValidationError("host-result run_dir does not match the resolved run directory")
        if report["status"] != "peer_running":
            raise ArtifactValidationError("waiting host-result status must be peer_running")
        if type(report["peer_pid"]) is not int or report["peer_pid"] <= 0:
            raise ArtifactValidationError("waiting host-result peer_pid must be a positive integer")
        if not isinstance(report["peer_report"], str) or Path(report["peer_report"]).resolve() != (run_dir / "peer-report.json").resolve():
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
            "claim_matrix_sha256",
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
        if report["schema_version"] != CONFIG.ARTIFACT_SCHEMA_VERSION or report["run_id"] != request["run_id"]:
            raise ArtifactValidationError("host-result schema_version/run_id does not match the v2 request")
        if not isinstance(report["run_dir"], str) or Path(report["run_dir"]).resolve() != run_dir.resolve():
            raise ArtifactValidationError("host-result run_dir does not match the resolved run directory")
        if report["peer_status"] not in {"ok", "peer_failed"}:
            raise ArtifactValidationError("host-result peer_status is invalid")
        expected_paths = {
            "normalization": run_dir / "peer-normalization.json",
            "claim_matrix": run_dir / "claim-matrix.json",
        }
        for key, expected in expected_paths.items():
            if not isinstance(report[key], str) or not report[key]:
                raise ArtifactValidationError(f"host-result {key} must be a non-empty string")
            if Path(report[key]).resolve() != expected.resolve():
                raise ArtifactValidationError(f"host-result {key} path does not match the run artifact")
        if request["adjudicator_enabled"]:
            adjudicator_path = report["adjudicator_report"]
            if not isinstance(adjudicator_path, str) or not adjudicator_path:
                raise ArtifactValidationError(
                    "host-result adjudicator_report must be a non-empty string when adjudication is enabled"
                )
            if Path(adjudicator_path).resolve() != (run_dir / "adjudicator-report.json").resolve():
                raise ArtifactValidationError(
                    "host-result adjudicator_report path does not match the run artifact"
                )
        elif report["adjudicator_report"] is not None:
            raise ArtifactValidationError(
                "host-result adjudicator_report must be null when adjudication is disabled"
            )
        if report["validation_status"] not in VALIDATION_STATUSES:
            raise ArtifactValidationError("host-result validation_status is invalid")
        if report["workspace_mutation"] is not None and not isinstance(report["workspace_mutation"], dict):
            raise ArtifactValidationError("host-result workspace_mutation must be an object or null")
        expected_mutation_digest = workspace_mutation_sha256(report["workspace_mutation"])
        if report["workspace_mutation_sha256"] != expected_mutation_digest:
            raise ArtifactValidationError("host-result workspace_mutation_sha256 is invalid")
        matrix_digest = report["claim_matrix_sha256"]
        if not isinstance(matrix_digest, str) or re.fullmatch(r"[0-9a-f]{64}", matrix_digest) is None:
            raise ArtifactValidationError("host-result claim_matrix_sha256 must be a lowercase SHA-256 digest")
        if phase == "done" and parse_utc_timestamp(report["completed_at"]) is None:
            raise ArtifactValidationError("host-result completed_at must be an ISO-8601 timestamp")
    else:
        raise ArtifactValidationError(
            "host-result phase must be waiting_for_peer, ready_for_synthesis, or done"
        )
    if report["finish_timeout_source"] not in FINISH_TIMEOUT_SOURCES:
        raise ArtifactValidationError("host-result finish_timeout_source is invalid")
    wait_seconds = report["finish_wait_seconds"]
    if (
        not isinstance(wait_seconds, (int, float))
        or isinstance(wait_seconds, bool)
        or not math.isfinite(float(wait_seconds))
        or not CONFIG.MIN_PEER_WAIT_SECONDS <= float(wait_seconds) <= CONFIG.SAFETY.MAX_AGENT_TIMEOUT_SECONDS + CONFIG.FINISH_TIMEOUT_GRACE_SECONDS
    ):
        raise ArtifactValidationError("host-result finish_wait_seconds must be finite and bounded")


def validate_peer_report_matches_request(report: dict[str, Any], request: dict[str, Any]) -> None:
    for key in CONFIG.load_peer_runtime().PEER_REPORT_REQUEST_BINDING_KEYS:
        if report.get(key) != request.get(key):
            raise PeerReportRequestMismatch(f"peer report {key} does not match host request")


def validate_claim_matrix(report: dict[str, Any], request: dict[str, Any]) -> None:
    required = {"schema_version", "run_id", "claims", "source_report_sha256"}
    missing = required - report.keys()
    if missing:
        raise ArtifactValidationError(f"claim-matrix missing required keys: {sorted(missing)}")
    unknown = set(report) - required
    if unknown:
        raise ArtifactValidationError(f"claim-matrix has unknown keys: {sorted(unknown)}")
    if report["schema_version"] != CONFIG.ARTIFACT_SCHEMA_VERSION:
        raise ArtifactValidationError("claim-matrix schema_version must be 2.0; legacy artifacts are not supported")
    if report["run_id"] != request.get("run_id"):
        raise ArtifactValidationError("claim-matrix run_id does not match host request")
    if not isinstance(report["claims"], list):
        raise ArtifactValidationError("claim-matrix claims must be an array")
    for claim in report["claims"]:
        validate_claim_shape(claim, "claim-matrix", require_source=True)
    sources = report["source_report_sha256"]
    keys = {"host_first_pass", "peer_report", "helper_reports", "adjudicator_report"}
    if not isinstance(sources, dict) or set(sources) != keys:
        raise ArtifactValidationError("claim-matrix source_report_sha256 must identify each canonical source")
    for key, digest in sources.items():
        if digest is None and key in {"helper_reports", "adjudicator_report"}:
            continue
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ArtifactValidationError(f"claim-matrix source {key} must have a lowercase SHA-256 digest")
    if request["adjudicator_enabled"] != (sources["adjudicator_report"] is not None):
        raise ArtifactValidationError("claim-matrix adjudicator source does not match the request")
    if sources["helper_reports"] is None and any(claim["source"] == "helper" for claim in report["claims"]):
        raise ArtifactValidationError("claim-matrix helper claims require an admitted helper-report digest")


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
    if report["schema_version"] != CONFIG.ARTIFACT_SCHEMA_VERSION:
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
    if report["status"] == "advisory_pending" and report != default_adjudicator_report(request, {"verdict": report["recommended_verdict"]}):
        raise ArtifactValidationError("advisory_pending must be the unmodified runtime placeholder")
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


def peer_findings_as_claims(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Expose peer findings as unverified matrix rows so synthesis cannot omit them."""

    rows = []
    for finding in report.get("findings", []):
        if not isinstance(finding, dict):
            continue
        files = "; ".join(str(item) for item in finding.get("files", []))
        evidence = f"{finding.get('details', '')} Files: {files or 'none'}. Recommendation: {finding.get('recommendation', '')}"
        rows.append({
            "claim": (
                f"[finding {finding.get('severity')}/{finding.get('confidence')} confidence] "
                f"{finding.get('title', '')}"
            ),
            "status": "plausible_unverified",
            "evidence": evidence.strip(),
            "source": "peer",
        })
    return rows


def load_helper_evidence(
    run_dir: Path, request: dict[str, Any]
) -> tuple[list[dict[str, Any]], str | None]:
    path = run_dir / "helper-reports.json"
    if not path.exists():
        return [], None
    data = load_json(path)
    return validate_helper_evidence(data, request)


def validate_helper_evidence(data: dict[str, Any], request: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    required = {"schema_version", "run_id", "reports"}
    if not isinstance(data, dict) or set(data) != required:
        raise ArtifactValidationError(f"helper-reports must contain exactly {sorted(required)}")
    if data["schema_version"] != CONFIG.ARTIFACT_SCHEMA_VERSION:
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
    return claims, canonical_artifact_sha256(data)


def build_claim_matrix(
    request: dict[str, Any],
    host_report: dict[str, Any],
    peer_report: dict[str, Any],
    helper_claims: list[dict[str, Any]] | None = None,
    adjudicator_report: dict[str, Any] | None = None,
    helper_reports_sha256: str | None = None,
) -> dict[str, Any]:
    claim_matrix = {
        "schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION,
        "run_id": request["run_id"],
        "source_report_sha256": {
            "host_first_pass": canonical_artifact_sha256(host_report),
            "peer_report": canonical_artifact_sha256(peer_report),
            "helper_reports": helper_reports_sha256,
            "adjudicator_report": (
                canonical_artifact_sha256(adjudicator_report) if adjudicator_report is not None else None
            ),
        },
        "claims": (
            claims_with_source(host_report, "host")
            + claims_with_source(peer_report, "peer")
            + peer_findings_as_claims(peer_report)
            + (helper_claims or [])
            + claims_with_source(adjudicator_report or {}, "adjudicator")
        ),
    }
    validate_claim_matrix(claim_matrix, request)
    return claim_matrix


def default_adjudicator_report(request: dict[str, Any], peer_report: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION,
        "run_id": request["run_id"],
        "status": "advisory_pending",
        "summary": "Host-local advisory adjudication is enabled for this run. This placeholder means no adjudicator artifact was supplied before finish.",
        "false_positives": [],
        "claims_needing_verification": [],
        "recommended_verdict": str(peer_report.get("verdict", "blocked")),
        "claims": [],
    }


def validate_peer_normalization(metadata: dict[str, Any]) -> None:
    if not isinstance(metadata, dict):
        raise ArtifactValidationError("peer-normalization must be an object")
    status = metadata.get("validation_status")
    common = {
        "schema_version",
        "source",
        "artifact_source",
        "input_bytes",
        "warnings",
        "validation_status",
    }
    required = common if status == "ok" else common | {"error"}
    if set(metadata) != required:
        raise ArtifactValidationError(
            "peer-normalization keys must match schema 2.0 exactly; "
            f"missing={sorted(required - set(metadata))}, unknown={sorted(set(metadata) - required)}"
        )
    if metadata["schema_version"] != CONFIG.ARTIFACT_SCHEMA_VERSION:
        raise ArtifactValidationError("peer-normalization schema_version must be 2.0")
    if status not in VALIDATION_STATUSES:
        raise ArtifactValidationError("peer-normalization validation_status is invalid")
    if metadata["artifact_source"] not in {"peer_report", "peer_raw"}:
        raise ArtifactValidationError("peer-normalization artifact_source must be peer_report or peer_raw")
    expected_sources = {"none"} if status != "ok" else (
        {"direct_json", "structured_output"} if metadata["artifact_source"] == "peer_raw" else {"direct_json"}
    )
    if metadata["source"] not in expected_sources:
        raise ArtifactValidationError(
            f"peer-normalization source must be one of {sorted(expected_sources)!r} for {status!r}"
        )
    if type(metadata["input_bytes"]) is not int or metadata["input_bytes"] < 0:
        raise ArtifactValidationError("peer-normalization input_bytes must be non-negative integer")
    if not isinstance(metadata["warnings"], list) or not all(
        isinstance(item, str) for item in metadata["warnings"]
    ):
        raise ArtifactValidationError("peer-normalization warnings must be an array of strings")
    if status != "ok" and (
        not isinstance(metadata["error"], str) or not metadata["error"].strip()
    ):
        raise ArtifactValidationError("failed peer-normalization error must be non-empty")


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
                CONFIG.STRICT_JSON.read_text(
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
            if normalization_path.exists() and normalization_path.stat().st_size:
                previous = load_json(normalization_path)
                validate_peer_normalization(previous)
                # A canonical runtime failure envelope is valid JSON, but that
                # does not turn the provider output that caused it into a
                # successful normalization. Keep its original failure receipt.
                if (previous["validation_status"] != "ok"
                        and normalized.report["status"] == "peer_failed"
                        and normalized.report["error"]["kind"] == previous["validation_status"]):
                    return normalized.report, previous["validation_status"]
                metadata["warnings"] = list(dict.fromkeys(previous["warnings"] + metadata["warnings"]))
            metadata["artifact_source"] = "peer_report"
            metadata["validation_status"] = "ok"
            CONFIG.STRICT_JSON.write(normalization_path, metadata)
            CONFIG.STRICT_JSON.write(peer_report_path, normalized.report)
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
    input_bytes = peer_report_path.stat().st_size if peer_report_path.exists() else 0
    CONFIG.STRICT_JSON.write(peer_report_path, peer_report)
    CONFIG.STRICT_JSON.write(
        normalization_path,
        {
            "schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION,
            "source": "none",
            "artifact_source": "peer_report",
            "input_bytes": input_bytes,
            "warnings": [],
            "validation_status": failure_kind,
            "error": message,
        },
    )
    return peer_report, failure_kind


def valid_terminal_peer_report(
    peer_runtime: Any,
    run_dir: Path,
    request: dict[str, Any],
) -> bool:
    path = run_dir / "peer-report.json"
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        normalized = peer_runtime.normalize_json_payload(
            CONFIG.STRICT_JSON.read_text(path, max_bytes=16 * 1024 * 1024).strip(),
            "direct_json",
        )
        peer_runtime.validate_peer_report(normalized.report)
        validate_peer_report_matches_request(normalized.report, request)
    except Exception:
        return False
    return True


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
    normalization = load_json(run_dir / "peer-normalization.json")
    peer_runtime.validate_peer_report(peer_report)
    validate_peer_report_matches_request(peer_report, request)
    validate_host_first_pass(host_report, request)
    validate_host_synthesis(host_synthesis, request)
    validate_claim_matrix(claim_matrix, request)
    adjudicator_path = run_dir / "adjudicator-report.json"
    adjudicator: dict[str, Any] | None = None
    if request["adjudicator_enabled"]:
        adjudicator = load_json(adjudicator_path)
        validate_adjudicator_report(adjudicator, request)
    elif adjudicator_path.exists():
        raise ArtifactValidationError(
            "adjudicator-report.json is forbidden because adjudication is disabled"
        )
    validate_peer_normalization(normalization)
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
    helper_claims, helper_digest = load_helper_evidence(run_dir, request)
    expected_claim_matrix = build_claim_matrix(
        request,
        host_report,
        peer_report,
        helper_claims,
        adjudicator,
        helper_digest,
    )
    if claim_matrix != expected_claim_matrix:
        raise ArtifactValidationError(
            "claim-matrix does not derive from the current host, peer, helper, and adjudicator reports; rerun finish"
        )
    current_digest = claim_matrix_sha256(expected_claim_matrix)
    if host_result["claim_matrix_sha256"] != current_digest:
        raise ArtifactValidationError("host-result does not bind the current claim-matrix; rerun finish")
    if host_synthesis["claim_matrix_sha256"] != current_digest:
        raise ArtifactValidationError(
            "host-synthesis does not bind the current claim-matrix; inspect the claims and resynthesize"
        )
    return peer_report
