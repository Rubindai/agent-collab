"""Strict run artifact validation and evidence assembly."""
from __future__ import annotations
import hashlib
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import validation as V
import host_config as CONFIG

MAX_JSON_ARTIFACT_BYTES = 16 * 1024 * 1024


COVERAGE_LIMITATIONS = [
    "Known high-volume generated and dependency directories are excluded.",
    "Large manifests retain complete digests but only a bounded path sample.",
    "Transient changes that were reverted before a snapshot are not observable.",
]


CLAIM_STATUSES = V.CLAIM_STATUSES


CLAIM_SHAPE_CONTRACT = (
    "expected claim/status/evidence shape: object with string 'claim', 'status' one of "
    f"{sorted(CLAIM_STATUSES)}, and string 'evidence'; join multiple evidence items with '; '; "
    "do not use id/type as substitutes or array-valued evidence"
)


CLAIM_SOURCES = {"host", "peer", "helper", "adjudicator"}


ADJUDICATOR_STATUSES = {"advisory_pending", "ok", "needs_human_input", "blocked"}


RECOMMENDED_VERDICTS = V.VERDICTS


VALIDATION_STATUSES = V.VALIDATION_STATUSES


FINISH_TIMEOUT_SOURCES = V.FINISH_TIMEOUT_SOURCES


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
    V.record(value, {"outcome": V.NONEMPTY, "quiescent": bool}, label,
             optional={"pgid": V.integer(1), "details": V.NONEMPTY}, error=ArtifactValidationError)


def validate_cancel_result(report: dict[str, Any], request: dict[str, Any], run_dir: Path) -> None:
    validate_artifact(report, request, "cancel-result", {
        "status": {"cancelled"}, "outcome": {"cancelled_quiescent"},
        "peer_was_alive": bool, "reason": V.NONEMPTY,
        "elapsed_seconds": (V.Rule(lambda v: V.NUMBER.accepts(v) and v >= 0, "a non-negative finite number"), type(None)),
        "process_cleanup": {"quiescent": {True}, "provider": dict, "wrapper": dict},
        "preserved_artifacts": dict.fromkeys(CANCEL_PRESERVED_ARTIFACTS, (V.DIGEST, type(None))),
        "workspace_mutation": (str, type(None)), "workspace_mutation_sha256": V.DIGEST, "completed_at": str,
    })
    cleanup = report["process_cleanup"]
    validate_cleanup_evidence(cleanup["provider"], "cancel-result provider cleanup")
    validate_cleanup_evidence(cleanup["wrapper"], "cancel-result wrapper cleanup")
    if cleanup["provider"]["quiescent"] is not True or cleanup["wrapper"]["quiescent"] is not True:
        raise ArtifactValidationError("cancel-result provider and wrapper cleanup must be quiescent")
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


def validate_peer_process(data: dict[str, Any], request: dict[str, Any], run_dir: Path) -> None:
    validate_artifact(data, request, "peer-process", {
        "pid": V.integer(1), "pgid": V.integer(1), "process_identity": dict,
        "run_dir": str, "host": V.ORIGINS, "peer": V.ORIGINS, "adjudicator_enabled": bool,
        "started_at": str, "started_at_epoch": V.NUMBER,
        "peer_timeout_seconds": V.number(CONFIG.MIN_PEER_TIMEOUT_SECONDS, CONFIG.SAFETY.MAX_AGENT_TIMEOUT_SECONDS),
        "owner_token": V.Rule(lambda v: type(v) is str and re.fullmatch(r"[0-9a-f]{32}", v) is not None,
                              "32 lowercase hexadecimal characters"),
        "request_sha256": V.DIGEST, "settings": {"local": V.NONEMPTY, "global": V.NONEMPTY},
        **dict.fromkeys(("repo_root", "peer_cli_version", "workspace_guard", "peer_report", "peer_raw",
                         "peer_normalization", "provider_process", "host_first_pass"), V.NONEMPTY),
    })
    if data["request_sha256"] != canonical_artifact_sha256(request):
        raise ArtifactValidationError("host-request differs from the launch-time request digest")
    for key in ("run_id", "host", "peer", "adjudicator_enabled"):
        if data[key] != request[key]:
            raise ArtifactValidationError(f"peer-process {key} does not match host request")
    if Path(data["run_dir"]).resolve() != run_dir.resolve():
        raise ArtifactValidationError("peer-process run_dir does not match the resolved run directory")
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
    timeout = data["peer_timeout_seconds"]
    if float(timeout) != float(request["peer_timeout_seconds"]):
        raise ArtifactValidationError("peer-process peer_timeout_seconds does not match host request")
    started_at = parse_utc_timestamp(data["started_at"])
    if abs(started_at - float(data["started_at_epoch"])) > 2:
        raise ArtifactValidationError("peer-process started_at and started_at_epoch disagree")
    expected_paths = {
        "peer_report": run_dir / "peer-report.json",
        "peer_raw": run_dir / "peer.raw.json",
        "peer_normalization": run_dir / "peer-normalization.json",
        "provider_process": run_dir / "provider-process.json",
        "host_first_pass": run_dir / "host-first-pass.json",
    }
    for key, expected_path in expected_paths.items():
        if Path(data[key]).resolve() != expected_path.resolve():
            raise ArtifactValidationError(f"peer-process {key} path does not match the v3 run layout")


def validate_provider_process(data: dict[str, Any], request: dict[str, Any]) -> None:
    if not isinstance(data, dict) or set(data) != PROVIDER_PROCESS_KEYS:
        raise ArtifactValidationError("provider-process keys must match schema 3.0 exactly")
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


def validate_claim_shape(claim: Any, context: str, require_source: bool = False, allow_extra: bool = False) -> None:
    fields = {**V.CLAIM, **({"source": CLAIM_SOURCES} if require_source else {})}
    try:
        V.record(claim, fields, f"{context} claim", error=ArtifactValidationError, allow_extra=allow_extra)
    except ArtifactValidationError as exc:
        raise ArtifactValidationError(f"{exc}; {CLAIM_SHAPE_CONTRACT}") from exc


def validate_artifact(report: Any, request: dict[str, Any], label: str, fields: dict[str, Any], *,
                      optional: dict[str, Any] | None = None, sourced_claims: bool = False) -> None:
    V.record(report, {"schema_version": V.VERSION, "run_id": str, **fields}, label,
             optional=optional, error=ArtifactValidationError)
    if report["run_id"] != request["run_id"]:
        raise ArtifactValidationError(f"{label} run_id does not match host request")
    for claim in report.get("claims", []):
        validate_claim_shape(claim, label, require_source=sourced_claims)


def validate_host_first_pass(report: dict[str, Any], request: dict[str, Any]) -> None:
    validate_artifact(report, request, "host-first-pass", {"summary": str, "claims": list})


def validate_host_synthesis(report: dict[str, Any], request: dict[str, Any]) -> None:
    validate_artifact(report, request, "host-synthesis", {
        "summary": V.NONBLANK, "verdict": RECOMMENDED_VERDICTS, "claims": list,
        "unresolved_risks": [str], "workspace_mutation_sha256": V.DIGEST,
        "claim_matrix_sha256": V.DIGEST, "final_answer_ready": {True},
    })


def validate_host_result(report: dict[str, Any], request: dict[str, Any], run_dir: Path) -> None:
    if not isinstance(report, dict):
        raise ArtifactValidationError("host-result must be an object")
    phase = report.get("phase")
    V.check(phase, str, "host-result phase", ArtifactValidationError)
    fields = {"run_dir": str, "phase": {"waiting_for_peer", "ready_for_synthesis", "done"},
              "finish_wait_seconds": V.number(CONFIG.MIN_PEER_TIMEOUT_SECONDS, CONFIG.SAFETY.MAX_AGENT_TIMEOUT_SECONDS + CONFIG.FINISH_TIMEOUT_GRACE_SECONDS),
              "finish_timeout_source": FINISH_TIMEOUT_SOURCES}
    if phase == "waiting_for_peer":
        fields.update(status={"peer_running"}, peer_pid=V.integer(1), peer_report=str, message=V.NONBLANK)
        paths = {"peer_report": "peer-report.json"}
    elif phase in {"ready_for_synthesis", "done"}:
        fields.update(peer_status={"ok", "peer_failed"}, validation_status=VALIDATION_STATUSES,
                      normalization=V.NONEMPTY, claim_matrix=V.NONEMPTY, claim_matrix_sha256=V.DIGEST,
                      adjudicator_report=(str, type(None)), workspace_mutation=(dict, type(None)),
                      workspace_mutation_sha256=V.DIGEST)
        if phase == "done":
            fields["completed_at"] = V.NONEMPTY
        paths = {"normalization": "peer-normalization.json", "claim_matrix": "claim-matrix.json"}
    else:
        raise ArtifactValidationError("host-result phase must be waiting_for_peer, ready_for_synthesis, or done")
    validate_artifact(report, request, "host-result", fields)
    if Path(report["run_dir"]).resolve() != run_dir.resolve():
        raise ArtifactValidationError("host-result run_dir does not match the resolved run directory")
    if phase != "waiting_for_peer":
        if request["adjudicator_enabled"]:
            V.check(report["adjudicator_report"], V.NONEMPTY, "host-result adjudicator_report", ArtifactValidationError)
            paths["adjudicator_report"] = "adjudicator-report.json"
        elif report["adjudicator_report"] is not None:
            raise ArtifactValidationError("host-result adjudicator_report must be null when adjudication is disabled")
        if report["workspace_mutation_sha256"] != workspace_mutation_sha256(report["workspace_mutation"]):
            raise ArtifactValidationError("host-result workspace_mutation_sha256 is invalid")
        if phase == "done" and parse_utc_timestamp(report["completed_at"]) is None:
            raise ArtifactValidationError("host-result completed_at must be an ISO-8601 timestamp")
    for key, filename in paths.items():
        if Path(report[key]).resolve() != (run_dir / filename).resolve():
            raise ArtifactValidationError(f"host-result {key} path does not match the run artifact")


def validate_peer_report_matches_request(report: dict[str, Any], request: dict[str, Any]) -> None:
    for key in CONFIG.PEER.PEER_REPORT_REQUEST_BINDING_KEYS:
        if report.get(key) != request.get(key):
            raise PeerReportRequestMismatch(f"peer report {key} does not match host request")


def validate_claim_matrix(report: dict[str, Any], request: dict[str, Any]) -> None:
    validate_artifact(report, request, "claim-matrix", {
        "claims": list,
        "source_report_sha256": {"host_first_pass": V.DIGEST, "peer_report": V.DIGEST,
                                 "helper_reports": (V.DIGEST, type(None)),
                                 "adjudicator_report": (V.DIGEST, type(None))},
    }, sourced_claims=True)
    sources = report["source_report_sha256"]
    if request["adjudicator_enabled"] != (sources["adjudicator_report"] is not None):
        raise ArtifactValidationError("claim-matrix adjudicator source does not match the request")
    if sources["helper_reports"] is None and any(claim["source"] == "helper" for claim in report["claims"]):
        raise ArtifactValidationError("claim-matrix helper claims require an admitted helper-report digest")


def validate_adjudicator_report(report: dict[str, Any], request: dict[str, Any]) -> None:
    validate_artifact(report, request, "adjudicator-report", {
        "status": ADJUDICATOR_STATUSES, "summary": str, "recommended_verdict": RECOMMENDED_VERDICTS,
        "false_positives": [str], "claims_needing_verification": [str],
    }, optional={"claims": list})
    if report["status"] == "advisory_pending" and report != default_adjudicator_report(request, {"verdict": report["recommended_verdict"]}):
        raise ArtifactValidationError("advisory_pending must be the unmodified runtime placeholder")


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


def validate_host_helper_budget(helpers: dict[str, Any] | None, adjudicator: dict[str, Any] | None,
                                request: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    """Count admitted helper reports, including one actual advisory report."""
    claims, digest = validate_helper_evidence(helpers, request) if helpers is not None else ([], None)
    if adjudicator is not None:
        validate_adjudicator_report(adjudicator, request)
    used = len(helpers["reports"]) if helpers else 0
    used += int(adjudicator is not None and adjudicator["status"] != "advisory_pending")
    limit = request["max_local_subagents"] if request["local_subagents_allowed"] else 0
    if used > limit:
        raise ArtifactValidationError(f"host helper reports including adjudication exceed the resolved per-run limit {limit}")
    return claims, digest


def validate_helper_evidence(data: dict[str, Any], request: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    validate_artifact(data, request, "helper-reports", {"reports": list})
    reports = data["reports"]
    helper_limit = request["max_local_subagents"] if request["local_subagents_allowed"] else 0
    if len(reports) > helper_limit:
        raise ArtifactValidationError(
            f"helper-reports contains {len(reports)} reports; the resolved per-run limit is {helper_limit}"
        )
    claims: list[dict[str, Any]] = []
    helper_names: set[str] = set()
    for report in reports:
        V.record(report, {"name": V.NONEMPTY, "summary": str, "claims": list}, "helper report", error=ArtifactValidationError)
        if report["name"] in helper_names:
            raise ArtifactValidationError(f"duplicate helper report name: {report['name']}")
        helper_names.add(report["name"])
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
            "peer-normalization keys must match schema 3.0 exactly; "
            f"missing={sorted(required - set(metadata))}, unknown={sorted(set(metadata) - required)}"
        )
    if metadata["schema_version"] != CONFIG.ARTIFACT_SCHEMA_VERSION:
        raise ArtifactValidationError("peer-normalization schema_version must be 3.0")
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


def validate_completion_artifacts(
    run_dir: Path,
    request: dict[str, Any],
    host_result: dict[str, Any],
    peer_runtime: Any,
    documents: dict[str, Any],
    host_synthesis: dict[str, Any],
) -> dict[str, Any]:
    peer_report = documents["peer-report.json"]
    host_report = documents["host-first-pass.json"]
    claim_matrix = documents["claim-matrix.json"]
    normalization = documents["peer-normalization.json"]
    peer_runtime.validate_peer_report(peer_report)
    validate_peer_report_matches_request(peer_report, request)
    validate_host_first_pass(host_report, request)
    validate_host_synthesis(host_synthesis, request)
    validate_claim_matrix(claim_matrix, request)
    adjudicator = documents["adjudicator-report.json"]
    if request["adjudicator_enabled"]:
        validate_adjudicator_report(adjudicator, request)
    elif adjudicator is not None:
        raise ArtifactValidationError(
            "adjudicator-report.json is forbidden because adjudication is disabled"
        )
    validate_peer_normalization(normalization)
    if normalization.get("validation_status") != host_result["validation_status"]:
        raise ArtifactValidationError("peer-normalization validation_status does not match host-result")
    if host_result["peer_status"] != peer_report["status"]:
        raise ArtifactValidationError("host-result peer_status does not match peer-report")
    workspace_mutation = documents["workspace-mutation.json"]
    if host_result["workspace_mutation"] != workspace_mutation:
        raise ArtifactValidationError("host-result workspace_mutation does not match the run artifact")
    if host_synthesis["workspace_mutation_sha256"] != host_result["workspace_mutation_sha256"]:
        raise ArtifactValidationError(
            "host-synthesis does not bind the current workspace mutation evidence; inspect the diagnostic and resynthesize"
        )
    helpers = documents["helper-reports.json"]
    helper_claims, helper_digest = validate_host_helper_budget(helpers, adjudicator, request)
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
            "claim-matrix does not derive from the current host, peer, helper, and adjudicator reports; resume advance"
        )
    current_digest = claim_matrix_sha256(expected_claim_matrix)
    if host_result["claim_matrix_sha256"] != current_digest:
        raise ArtifactValidationError("host-result does not bind the current claim-matrix; resume advance")
    if host_synthesis["claim_matrix_sha256"] != current_digest:
        raise ArtifactValidationError(
            "host-synthesis does not bind the current claim-matrix; inspect the claims and resynthesize"
        )
    return peer_report
