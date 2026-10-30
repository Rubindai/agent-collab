"""Atomic worktree registry ownership and bounded lifecycle locks."""
from __future__ import annotations
import os
import re
import secrets
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
import host_config as CONFIG
import artifacts as ARTIFACTS

WORKSPACE_GUARD_SCHEMA_VERSION = "2.0"


WORKSPACE_GUARD_MAX_BYTES = 1_000_000


WORKSPACE_GUARD_IDENTITY_MAX_BYTES = 4096


WORKSPACE_GUARD_GIT_PATH = "agent-collab/active-v2.json"


WORKSPACE_GUARD_LOCK_TIMEOUT_SECONDS = 10.0


WORKSPACE_GUARD_LOCK_POLL_SECONDS = 0.02


RUN_OPERATION_LOCK_TIMEOUT_SECONDS = 10.0


RUN_OPERATION_LOCK_POLL_SECONDS = 0.02


WORKSPACE_GUARD_KEYS = {
    "owner_token",
    "schema_version",
    "run_id",
    "repo_root",
    "run_dir",
    "host",
    "phase",
    "created_at",
    "launcher_identity",
    "peer_identity",
}


def workspace_guard_path(repo_root: Path) -> Path:
    completed = CONFIG.run_git_probe(["rev-parse", "--git-path", WORKSPACE_GUARD_GIT_PATH], cwd=repo_root)
    if completed is None and CONFIG.load_snapshot_runtime().has_git_metadata(repo_root):
        # Without Git the guard would move out of the repository, where it no
        # longer excludes runs started from an environment that has Git.
        raise SystemExit("git is required inside a Git repository, but it is not on PATH")
    if completed is not None and completed.returncode == 0 and completed.stdout.strip():
        candidate = Path(completed.stdout.strip())
        candidate = candidate if candidate.is_absolute() else repo_root / candidate
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")).expanduser()
        candidate = base / "agent-collab" / "workspace-guards" / f"{CONFIG.repo_storage_id(repo_root)}.json"
    # Resolve only the parent: a symlinked guard file must be rejected, not
    # followed to a redirected guard or lock location.
    return candidate.parent.resolve() / candidate.name


def workspace_guard_lock_path(repo_root: Path) -> Path:
    guard_path = workspace_guard_path(repo_root)
    return guard_path.with_name(f"{guard_path.name}.lock")


@contextmanager
def run_operation_lock(run_dir: Path):
    """Serialize short finalization mutations without blocking the long peer wait."""

    CONFIG.require_supported_platform()
    CONFIG.STRICT_JSON.ensure_private_directory(run_dir)
    lock_path = run_dir / ".workflow.lock"
    descriptor = CONFIG.STRICT_JSON.open_lock_file(lock_path)
    acquired = False
    deadline = time.monotonic() + RUN_OPERATION_LOCK_TIMEOUT_SECONDS
    try:
        while not CONFIG.STRICT_JSON.try_lock(descriptor):
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"timed out acquiring Agent Collab run operation lock: {lock_path}"
                )
            time.sleep(RUN_OPERATION_LOCK_POLL_SECONDS)
        acquired = True
        yield
    finally:
        if acquired:
            try:
                CONFIG.STRICT_JSON.unlock(descriptor)
            except OSError:
                pass
        os.close(descriptor)


@contextmanager
def workspace_guard_transaction(repo_root: Path):
    """Serialize every guard read-modify-write transition across processes."""

    lock_path = workspace_guard_lock_path(repo_root)
    CONFIG.STRICT_JSON.ensure_private_directory(lock_path.parent)
    descriptor = CONFIG.STRICT_JSON.open_lock_file(lock_path)
    acquired = False
    deadline = time.monotonic() + WORKSPACE_GUARD_LOCK_TIMEOUT_SECONDS
    try:
        while not CONFIG.STRICT_JSON.try_lock(descriptor):
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"timed out acquiring Agent Collab workspace guard lock: {lock_path}"
                )
            time.sleep(WORKSPACE_GUARD_LOCK_POLL_SECONDS)
        acquired = True
        yield
    finally:
        if acquired:
            try:
                CONFIG.STRICT_JSON.unlock(descriptor)
            except OSError:
                pass
        os.close(descriptor)


def _read_workspace_guards_unlocked(repo_root: Path) -> tuple[Path, dict[str, dict[str, Any]]]:
    path = workspace_guard_path(repo_root)
    if path.is_symlink():
        raise SystemExit(f"Agent Collab workspace guard must not be a symlink: {path}")
    if not path.exists():
        return path, {}
    try:
        registry = CONFIG.STRICT_JSON.load(path, max_bytes=WORKSPACE_GUARD_MAX_BYTES)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"invalid active Agent Collab workspace guard at {path}: {exc}") from exc
    if (not isinstance(registry, dict) or set(registry) != {"schema_version", "runs"}
            or registry.get("schema_version") != WORKSPACE_GUARD_SCHEMA_VERSION
            or not isinstance(registry.get("runs"), dict)):
        raise SystemExit(f"invalid or legacy active Agent Collab workspace guard at {path}; close active runs with the version that started them")
    for run_id, parsed in registry["runs"].items():
        if (not isinstance(parsed, dict) or set(parsed) != WORKSPACE_GUARD_KEYS
                or parsed.get("schema_version") != WORKSPACE_GUARD_SCHEMA_VERSION):
            raise SystemExit(f"invalid active Agent Collab workspace guard entry {run_id!r} at {path}; close incompatible runs with the version that started them")
        if not isinstance(parsed["owner_token"], str) or re.fullmatch(r"[0-9a-f]{32}", parsed["owner_token"]) is None:
            raise SystemExit(f"invalid owner token for {run_id!r} in workspace guard at {path}")
        if not isinstance(parsed["run_id"], str) or not CONFIG.is_safe_run_id(parsed["run_id"]) or parsed["run_id"] != run_id:
            raise SystemExit(f"invalid active Agent Collab run_id for {run_id!r} in workspace guard at {path}")
        if (not isinstance(parsed["host"], str) or not isinstance(parsed["phase"], str)
                or parsed["host"] not in CONFIG.ORIGINS or parsed["phase"] not in {"starting", "peer_running", "ready_for_synthesis"}):
            raise SystemExit(f"invalid active Agent Collab host/phase for {run_id!r} in workspace guard at {path}")
        if not isinstance(parsed["repo_root"], str) or Path(parsed["repo_root"]).resolve() != repo_root.resolve():
            raise SystemExit(f"active Agent Collab workspace guard repo_root mismatch for {run_id!r} at {path}")
        if not isinstance(parsed["run_dir"], str) or not parsed["run_dir"]:
            raise SystemExit(f"invalid active Agent Collab run_dir for {run_id!r} in workspace guard at {path}")
        if ARTIFACTS.parse_utc_timestamp(parsed["created_at"]) is None:
            raise SystemExit(f"invalid active Agent Collab created_at for {run_id!r} in workspace guard at {path}")
        try:
            validate_guard_identity(parsed["launcher_identity"])
            if parsed["peer_identity"] is not None:
                validate_guard_identity(parsed["peer_identity"])
        except (ValueError, TypeError) as exc:
            raise SystemExit(f"invalid process identity for {run_id!r} in workspace guard at {path}: {exc}; close incompatible runs with the version that started them") from exc
        if parsed["phase"] == "starting" and parsed["peer_identity"] is not None:
            raise SystemExit(f"starting Agent Collab guard must not contain peer_identity for {run_id!r} at {path}")
        if parsed["phase"] != "starting" and parsed["peer_identity"] is None:
            raise SystemExit(f"active Agent Collab guard is missing peer_identity for {run_id!r} at {path}")
    return path, registry["runs"]


def validate_guard_identity(identity: Any) -> None:
    CONFIG.SAFETY.validate_process_identity(identity)
    if len(CONFIG.STRICT_JSON.dumps(identity).encode("utf-8")) > WORKSPACE_GUARD_IDENTITY_MAX_BYTES:
        raise ValueError("workspace guard process identity exceeds 4 KiB")


def registry_capacity_bytes(runs: dict[str, dict[str, Any]]) -> int:
    """Reserve every admitted entry's largest possible lifecycle representation."""
    size = len((CONFIG.STRICT_JSON.dumps({"schema_version": WORKSPACE_GUARD_SCHEMA_VERSION, "runs": runs}) + "\n").encode("utf-8"))
    for entry in runs.values():
        current_identity_size = len(CONFIG.STRICT_JSON.dumps(entry["peer_identity"]).encode("utf-8"))
        # Identities have at most seven scalar fields. 128 also covers the additional
        # indentation when the bounded object is nested inside the registry.
        size += WORKSPACE_GUARD_IDENTITY_MAX_BYTES + 128 - current_identity_size
        size += len("ready_for_synthesis") - len(entry["phase"])
    return size


def _write_workspace_guards_unlocked(path: Path, runs: dict[str, dict[str, Any]]) -> None:
    if runs:
        registry = {"schema_version": WORKSPACE_GUARD_SCHEMA_VERSION, "runs": runs}
        if len((CONFIG.STRICT_JSON.dumps(registry) + "\n").encode("utf-8")) > WORKSPACE_GUARD_MAX_BYTES:
            raise SystemExit("Agent Collab workspace guard registry exceeds the 1 MB limit")
        CONFIG.STRICT_JSON.write(path, registry)
    else:
        path.unlink(missing_ok=True)


def read_workspace_guard(repo_root: Path, run_id: str) -> tuple[Path, dict[str, Any] | None]:
    with workspace_guard_transaction(repo_root):
        path, runs = _read_workspace_guards_unlocked(repo_root)
        return path, runs.get(run_id)


def read_workspace_guards(repo_root: Path) -> tuple[Path, dict[str, dict[str, Any]]]:
    with workspace_guard_transaction(repo_root):
        return _read_workspace_guards_unlocked(repo_root)


def require_workspace_guard_available(repo_root: Path) -> None:
    # Validate the registry without probing every other session's launcher.
    read_workspace_guards(repo_root)


def recover_workspace_guard(repo_root: Path, run_id: str) -> Path:
    CONFIG.require_safe_run_id(run_id)
    with workspace_guard_transaction(repo_root):
        path, runs = _read_workspace_guards_unlocked(repo_root)
        active = runs.get(run_id)
        if active is None:
            raise SystemExit(f"active Agent Collab workspace guard is missing for run {run_id}")
        if active["phase"] != "starting" or CONFIG.SAFETY.process_identity_state(active["launcher_identity"]) != "dead_or_replaced":
            raise SystemExit(f"cannot recover {run_id}: only a starting entry with a proven dead launcher can be recovered")
        del runs[run_id]
        _write_workspace_guards_unlocked(path, runs)
    return path


def acquire_workspace_guard(repo_root: Path, run_id: str, host: str, run_dir: Path, *, owner_token: str | None = None) -> Path:
    CONFIG.require_safe_run_id(run_id)
    if host not in CONFIG.ORIGINS or (owner_token is not None and (not isinstance(owner_token, str) or re.fullmatch(r"[0-9a-f]{32}", owner_token) is None)):
        raise ValueError("invalid workspace guard host or owner token")
    payload = {
        "schema_version": WORKSPACE_GUARD_SCHEMA_VERSION,
        "owner_token": owner_token if owner_token is not None else secrets.token_hex(16),
        "run_id": run_id,
        "repo_root": str(repo_root.resolve()),
        "run_dir": str(run_dir.resolve()),
        "host": host,
        "phase": "starting",
        "created_at": ARTIFACTS.utc_timestamp(),
        "launcher_identity": CONFIG.SAFETY.process_identity(os.getpid()),
        "peer_identity": None,
    }
    validate_guard_identity(payload["launcher_identity"])
    with workspace_guard_transaction(repo_root):
        path, runs = _read_workspace_guards_unlocked(repo_root)
        active = runs.get(run_id)
        if active is not None:
            if active["phase"] != "starting" or CONFIG.SAFETY.process_identity_state(active["launcher_identity"]) != "dead_or_replaced":
                raise SystemExit(f"Agent Collab run ID is already active: {run_id} guard={path}")
            # Recover only the exact stale starting entry while holding the lock.
        runs[run_id] = payload
        if registry_capacity_bytes(runs) > WORKSPACE_GUARD_MAX_BYTES:
            raise SystemExit("Agent Collab workspace guard registry has no capacity for another run and its lifecycle updates")
        _write_workspace_guards_unlocked(path, runs)
    return path


def update_workspace_guard(
    repo_root: Path,
    run_id: str,
    *,
    owner: dict[str, Any],
    phase: str,
    peer_identity: dict[str, Any],
) -> Path:
    if phase not in {"peer_running", "ready_for_synthesis"}:
        raise ValueError("workspace guard update phase is invalid")
    with workspace_guard_transaction(repo_root):
        path, runs = _read_workspace_guards_unlocked(repo_root)
        active = runs.get(run_id)
        if active is None:
            raise SystemExit(f"cannot update missing Agent Collab workspace guard for {run_id}")
        validate_guard_owner(active, owner)
        validate_guard_identity(peer_identity)
        runs[run_id] = {**active, "phase": phase, "peer_identity": peer_identity}
        _write_workspace_guards_unlocked(path, runs)
    return path


def validate_guard_owner(active: dict[str, Any], owner: dict[str, Any]) -> None:
    if any(active[key] != owner.get(key) for key in ("run_id", "host", "owner_token")) or any(
        not isinstance(owner.get(key), str) or Path(active[key]).resolve() != Path(owner[key]).resolve()
        for key in ("repo_root", "run_dir")
    ):
        raise SystemExit(f"Agent Collab guard owner mismatch for {active['run_id']}; the current run instance was preserved")


def require_workspace_guard_owner(repo_root: Path, run_id: str, *, owner: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    with workspace_guard_transaction(repo_root):
        path, runs = _read_workspace_guards_unlocked(repo_root)
        active = runs.get(run_id)
        if active is None:
            raise SystemExit(f"active Agent Collab workspace guard is missing for run {run_id}")
        validate_guard_owner(active, owner)
        return path, active


def release_workspace_guard(repo_root: Path, run_id: str, *, owner: dict[str, Any]) -> Path:
    with workspace_guard_transaction(repo_root):
        path, runs = _read_workspace_guards_unlocked(repo_root)
        if run_id not in runs:
            raise SystemExit(f"active Agent Collab workspace guard is missing for run {run_id}")
        validate_guard_owner(runs[run_id], owner)
        del runs[run_id]
        _write_workspace_guards_unlocked(path, runs)
    return path
