"""Owned process-tree termination and quiescence verification."""
from __future__ import annotations
import subprocess
import time
from pathlib import Path
from typing import Any
import host_config as CONFIG
import artifacts as ARTIFACTS


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
                CONFIG.ISOLATION.cleanup_state(isolation_path)
            except (OSError, ValueError, RuntimeError) as exc:
                raise ARTIFACTS.ArtifactValidationError(f"isolated workspace cleanup failed: {exc}") from exc
        if CONFIG.SAFETY.process_group_alive(int(process_info["pgid"])):
            raise ARTIFACTS.ArtifactValidationError("peer wrapper group still has live members")
    if request["workspace_mode"] == "isolated":
        if provider["status"] != "pending" and not isolation_path.exists():
            raise ARTIFACTS.ArtifactValidationError("isolated provider is missing its cleanup state")
        try:
            CONFIG.ISOLATION.cleanup_state(isolation_path)
        except (OSError, ValueError, RuntimeError) as exc:
            raise ARTIFACTS.ArtifactValidationError(f"isolated workspace cleanup failed: {exc}") from exc
    if provider["status"] == "pending":
        return provider
    if provider["status"] != "quiescent":
        raise ARTIFACTS.ArtifactValidationError(
            f"provider process group is not terminal and quiescent: {provider['status']}"
        )
    return provider


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
            if not CONFIG.SAFETY.reap_process(process):
                cleanup = {**cleanup, "outcome": "leader_not_reaped", "quiescent": False}
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as cleanup_error:
            cleanup = {
                "outcome": "cleanup_exception",
                "quiescent": False,
                "details": str(cleanup_error),
            }
    return cleanup


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
            CONFIG.STRICT_JSON.write(run_dir / "provider-process.json", provider)
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
            CONFIG.STRICT_JSON.write(run_dir / "provider-process.json", provider)

    if (
        request["workspace_mode"] == "isolated"
        and provider_cleanup.get("quiescent")
        and not process_identity_matches(process_info)
    ):
        try:
            CONFIG.ISOLATION.cleanup_state(run_dir / "isolation-state.json")
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
            CONFIG.ISOLATION.cleanup_state(run_dir / "isolation-state.json")
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



def guard_wrapper_proof(guard: dict[str, Any], process: dict[str, Any]) -> bool:
    """A ready guard records quiescence for this exact wrapper identity."""
    if guard["phase"] != "ready_for_synthesis":
        return False
    if guard["peer_identity"] != process["process_identity"]:
        raise ValueError("ready-for-synthesis workspace guard contains an invalid wrapper-quiescence proof")
    return True
