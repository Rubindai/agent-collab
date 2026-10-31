#!/usr/bin/env python3
"""Disposable Linux copy-on-write filesystem for a full-capability peer.

The provider sees overlay views of HOME and the repository at their original
paths, plus a private per-run temporary directory for its outputs. Host-owned
run artifacts are not bound writable. This only discards writes made through
those paths: sockets, devices, networking, and external services (for example
a Docker daemon or the systemd user manager) can still change the host.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import validation as V
from typing import Any

sys.dont_write_bytecode = True


class IsolationUnavailable(RuntimeError):
    pass


def _mounted(path: Path) -> bool:
    return os.path.ismount(path)


def _unmount(path: Path) -> None:
    if not _mounted(path):
        return
    tool = shutil.which("fusermount3") or shutil.which("fusermount")
    if tool is None:
        raise IsolationUnavailable("fusermount3 or fusermount is required for cleanup")
    completed = subprocess.run([tool, "-u", str(path)], capture_output=True, text=True, timeout=10)
    if completed.returncode != 0 or _mounted(path):
        raise IsolationUnavailable(f"failed to unmount {path}: {completed.stderr.strip()}")


def _mount(lower: Path, base: Path, deadline: float | None = None) -> tuple[Path, subprocess.Popen[bytes]]:
    tool = shutil.which("fuse-overlayfs")
    if tool is None:
        raise IsolationUnavailable("fuse-overlayfs is not installed")
    if any(character in str(lower) for character in (",", ":", "\\", "\n")):
        raise IsolationUnavailable("overlay lower path contains an unsupported character")
    base.mkdir(mode=0o700)
    os.chmod(base, 0o700)
    upper, work, merged = (base / item for item in ("upper", "work", "merged"))
    for directory in (upper, work, merged):
        directory.mkdir(mode=0o700)
        os.chmod(directory, 0o700)
    process = subprocess.Popen(
        [tool, "-f", "-o", f"lowerdir={lower},upperdir={upper},workdir={work}", str(merged)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    mount_deadline = min(time.monotonic() + 10, deadline if deadline is not None else float("inf"))
    while time.monotonic() < mount_deadline:
        if _mounted(merged):
            return merged, process
        if process.poll() is not None:
            detail = process.stderr.read().decode("utf-8", "replace").strip() if process.stderr else ""
            process.wait(timeout=5)
            if process.stderr:
                process.stderr.close()
            raise IsolationUnavailable(f"fuse-overlayfs failed: {detail}")
        time.sleep(0.05)
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    if process.stderr:
        process.stderr.close()
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("peer deadline elapsed during isolated workspace mount")
    raise IsolationUnavailable("fuse-overlayfs mount did not initialize")


def _write_state(path: Path, value: dict[str, Any]) -> None:
    # State is only written by the wrapper, in its private run directory.
    if path.is_symlink():
        raise IsolationUnavailable("isolation state destination is a symlink")
    # A unique name keeps a temporary file left by an interrupted write from
    # blocking every later state transition.
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def cleanup_state(path: Path) -> None:
    if not path.exists():
        return
    if path.is_symlink():
        raise IsolationUnavailable("isolation state is a symlink")
    if path.stat().st_size > 16_384:
        raise IsolationUnavailable("isolation state exceeds its size limit")
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise IsolationUnavailable("duplicate isolation state key")
            result[key] = value
        return result
    def no_constant(value: str) -> Any:
        raise IsolationUnavailable(f"nonfinite isolation state value: {value}")
    data = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=no_duplicates,
        parse_constant=no_constant,
    )
    if (not isinstance(data, dict)
            or set(data) != {"schema_version", "scratch", "mounts", "status"}
            or data["schema_version"] != V.RUN_SCHEMA_VERSION
            or not isinstance(data["scratch"], str)
            or not isinstance(data["status"], str)
            or not isinstance(data["mounts"], list)
            or not all(isinstance(item, str) for item in data["mounts"])):
        raise IsolationUnavailable("invalid isolation state")
    scratch = Path(data["scratch"])
    if (not scratch.is_absolute() or scratch.parent != Path("/tmp").resolve()
            or not scratch.name.startswith("agent-collab-cow-") or scratch.is_symlink()):
        raise IsolationUnavailable("invalid isolation scratch path")
    mounts = [Path(item) for item in data["mounts"]]
    expected_mounts = [scratch / "home" / "merged", scratch / "repo" / "merged"]
    if mounts != expected_mounts or data["status"] not in {"preparing", "mounted", "cleaned"}:
        raise IsolationUnavailable("invalid isolation mount path")
    for mount in reversed(mounts):
        _unmount(mount)
    if scratch.exists():
        shutil.rmtree(scratch)
    if data["status"] != "cleaned":
        _write_state(path, {**data, "status": "cleaned"})


def credential_passthrough(peer: str, env: dict[str, str]) -> list[Path]:
    """Return the provider credential file to keep writable on the host.

    Providers rotate refresh tokens. A refresh written only into the discarded
    HOME overlay would leave the host holding an already-used refresh token, so
    the real credential file is bound through. Codex rewrites auth.json in
    place; Claude falls back to an in-place write when a rename onto a bind
    mount fails with EBUSY, so either order of host and peer refresh keeps the
    host's stored credential valid.
    """

    home = Path(env.get("HOME") or Path.home()).expanduser()
    if peer == "codex":
        candidate = Path(env.get("CODEX_HOME") or home / ".codex").expanduser() / "auth.json"
    elif peer == "claude":
        candidate = Path(env.get("CLAUDE_CONFIG_DIR") or home / ".claude").expanduser() / ".credentials.json"
    else:
        raise IsolationUnavailable(f"unsupported peer for credential passthrough: {peer}")
    if candidate.is_symlink() or not candidate.is_file():
        return []
    return [candidate.resolve()]


class Workspace:
    def __init__(self, repo_root: Path, run_dir: Path, deadline: float | None = None):
        self.repo_root = repo_root.resolve()
        self.run_dir = run_dir.resolve()
        self.home = Path.home().resolve()
        self.state_path = self.run_dir / "isolation-state.json"
        self.deadline = deadline
        self.scratch: Path | None = None
        self.mounts: list[Path] = []
        self.processes: list[subprocess.Popen[bytes]] = []

    def __enter__(self) -> "Workspace":
        if sys.platform != "linux" or shutil.which("bwrap") is None:
            raise IsolationUnavailable("Linux and bwrap are required for the isolated workspace")
        if not self.home.is_dir() or not self.repo_root.is_dir() or not self.run_dir.is_dir():
            raise IsolationUnavailable("HOME, repository, and run directory must exist")
        self.scratch = Path(tempfile.mkdtemp(prefix="agent-collab-cow-", dir="/tmp"))
        os.chmod(self.scratch, 0o700)
        try:
            _write_state(self.state_path, {
                "schema_version": V.RUN_SCHEMA_VERSION, "scratch": str(self.scratch),
                "mounts": [str(self.scratch / name / "merged") for name in ("home", "repo")],
                "status": "preparing",
            })
            for name, lower in (("home", self.home), ("repo", self.repo_root)):
                merged, process = _mount(lower, self.scratch / name, self.deadline)
                self.mounts.append(merged)
                self.processes.append(process)
            _write_state(self.state_path, {
                "schema_version": V.RUN_SCHEMA_VERSION, "scratch": str(self.scratch),
                "mounts": [str(item) for item in self.mounts], "status": "mounted",
            })
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def command(
        self, provider: list[str], temporary: Path, passthrough: list[Path] | None = None,
    ) -> list[str]:
        if len(self.mounts) != 2:
            raise IsolationUnavailable("workspace mounts are incomplete")
        # Later binds override the HOME and repository overlays at their paths.
        credential_binds: list[str] = []
        for path in passthrough or []:
            credential_binds += ["--bind", str(path), str(path)]
        return [
            "bwrap", "--die-with-parent", "--unshare-pid", "--ro-bind", "/", "/",
            "--dev", "/dev", "--proc", "/proc",
            "--tmpfs", "/tmp", "--tmpfs", "/var/tmp",
            "--bind", str(temporary), str(temporary),
            "--bind", str(self.mounts[0]), str(self.home),
            "--bind", str(self.mounts[1]), str(self.repo_root),
            *credential_binds,
            "--chdir", str(self.repo_root), "--", *provider,
        ]

    def __exit__(self, _kind: Any, _value: Any, _traceback: Any) -> None:
        # Clean up from the wrapper's own in-memory mount list, never from a
        # state file that could have been rewritten after it was recorded.
        try:
            for mount in reversed(self.mounts):
                _unmount(mount)
            if self.scratch is not None and self.scratch.exists():
                shutil.rmtree(self.scratch)
            if self.scratch is not None and self.state_path.exists():
                _write_state(self.state_path, {
                    "schema_version": V.RUN_SCHEMA_VERSION, "scratch": str(self.scratch),
                    "mounts": [str(self.scratch / name / "merged") for name in ("home", "repo")],
                    "status": "cleaned",
                })
        finally:
            for process in self.processes:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                if process.stderr:
                    process.stderr.close()


def preflight(repo_root: Path) -> dict[str, Any]:
    if sys.platform != "linux":
        return {"status": "sandbox_unavailable", "backend": "linux_fuse_overlay_bwrap", "details": "isolated workspace currently requires Linux"}
    for name in ("fuse-overlayfs", "bwrap"):
        if shutil.which(name) is None:
            return {"status": "sandbox_unavailable", "backend": "linux_fuse_overlay_bwrap", "details": f"{name} is not installed"}
    if shutil.which("fusermount3") is None and shutil.which("fusermount") is None:
        return {"status": "sandbox_unavailable", "backend": "linux_fuse_overlay_bwrap", "details": "fusermount is not installed"}
    if any(character in str(path) for path in (repo_root.resolve(), Path.home().resolve()) for character in (",", ":", "\\", "\n")):
        return {"status": "sandbox_unavailable", "backend": "linux_fuse_overlay_bwrap", "details": "workspace path contains an unsupported overlay character"}
    try:
        with tempfile.TemporaryDirectory(prefix="agent-collab-cow-preflight-", dir="/tmp") as temporary:
            root = Path(temporary)
            lower = root / "lower"
            lower.mkdir()
            (lower / "original").write_text("unchanged", encoding="utf-8")
            merged, process = _mount(lower, root / "overlay")
            try:
                command = ["bwrap", "--die-with-parent", "--ro-bind", "/", "/", "--bind", str(merged), str(lower), "--", "sh", "-c", f"printf changed > {lower}/original; printf new > {lower}/new"]
                completed = subprocess.run(command, capture_output=True, text=True, timeout=15)
                if completed.returncode != 0:
                    raise IsolationUnavailable(f"bwrap write probe failed: {completed.stderr.strip()}")
                if (lower / "original").read_text(encoding="utf-8") != "unchanged" or (lower / "new").exists():
                    raise IsolationUnavailable("copy-on-write probe changed the lower directory")
                if (merged / "original").read_text(encoding="utf-8") != "changed":
                    raise IsolationUnavailable("copy-on-write probe did not retain upper-layer writes")
            finally:
                _unmount(merged)
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=5)
                if process.stderr:
                    process.stderr.close()
    except (OSError, subprocess.TimeoutExpired, IsolationUnavailable) as exc:
        return {"status": "sandbox_unavailable", "backend": "linux_fuse_overlay_bwrap", "details": str(exc)}
    return {"status": "available", "backend": "linux_fuse_overlay_bwrap", "details": "copy-on-write write/discard probe passed"}
