#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
import select
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SNAPSHOT_HEADER = "agent_collab_workspace_snapshot_v2"
IGNORED_DIR_NAMES = {
    ".cache",
    ".git",
    ".hg",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".temp",
    ".tmp",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "htmlcov",
    "node_modules",
    "target",
    "temp",
    "tmp",
    "venv",
}
IGNORED_REL_PREFIXES = {
    ("tools", "agent-collab", "runs"),
}
IGNORED_REL_FILES = {
    ("tools", "agent-collab", "settings.local.json"),
}
DEFAULT_SNAPSHOT_TIMEOUT_SECONDS = 300.0
MAX_RETAINED_ENTRIES = 1024
MAX_RETAINED_ENTRY_BYTES = 512 * 1024
MAX_RETAINED_STATUS_BYTES = 512 * 1024
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _load_strict_json_runtime() -> Any:
    path = Path(__file__).resolve().with_name("strict_json.py")
    spec = importlib.util.spec_from_file_location("agent_collab_snapshot_strict_json", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load strict JSON runtime: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


STRICT_JSON = _load_strict_json_runtime()


def _load_safety_runtime() -> Any:
    path = Path(__file__).resolve().with_name("safety.py")
    spec = importlib.util.spec_from_file_location("agent_collab_snapshot_safety", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Agent Collab safety runtime: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SAFETY = _load_safety_runtime()


def _snapshot_deadline(deadline: float | None) -> float:
    return time.monotonic() + DEFAULT_SNAPSHOT_TIMEOUT_SECONDS if deadline is None else float(deadline)


def _remaining_seconds(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("workspace snapshot exceeded its finite deadline")
    return remaining


def _resolve(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _absolute(path: Path) -> Path:
    path = path.expanduser()
    if path.is_absolute():
        return path
    return Path.cwd() / path


def _normalize_ignored_paths(paths: Iterable[Path] | None) -> list[Path]:
    ignored: list[Path] = []
    for path in paths or []:
        if path is None:
            continue
        ignored.append(_resolve(Path(path)))
    return ignored


def _rel_parts(repo_root: Path, path: Path) -> tuple[str, ...]:
    return path.relative_to(repo_root).parts


def _has_prefix(parts: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    return len(parts) >= len(prefix) and parts[: len(prefix)] == prefix


def should_ignore(path: Path, repo_root: Path, ignored_paths: Iterable[Path] | None = None) -> bool:
    repo_root = _resolve(repo_root)
    path = _absolute(path)
    for ignored in _normalize_ignored_paths(ignored_paths):
        if _resolve(path) == ignored or _resolve(path).is_relative_to(ignored):
            return True
    try:
        parts = _rel_parts(repo_root, path)
    except ValueError:
        return True
    if not parts:
        return False
    if any(part in IGNORED_DIR_NAMES for part in parts[:-1]):
        return True
    if parts[-1] in IGNORED_DIR_NAMES:
        return True
    if parts in IGNORED_REL_FILES:
        return True
    return any(_has_prefix(parts, prefix) for prefix in IGNORED_REL_PREFIXES)


# Disable fsmonitor and content conversion programs during host snapshots.
# Filter drivers are disabled dynamically below; diff disables textconv and
# external drivers. Inherited GIT_* variables must not redirect the scan.
GIT_BASE_COMMAND = (
    "git", "--no-optional-locks",
    "-c", "diff.autoRefreshIndex=false",
    "-c", "core.fsmonitor=false",
    "-c", "core.untrackedCache=false",
)


def _git_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _printable(value: str) -> str:
    """Render POSIX names losslessly as valid UTF-8 for JSON artifacts."""
    return value.encode("utf-8", "surrogateescape").decode("utf-8", "backslashreplace")


def _git_filter_drivers(repo_root: Path, deadline: float) -> set[str]:
    """Include initialized nested submodules before Git recursively reads them."""
    drivers: set[str] = set()
    pending = [repo_root]
    visited: set[Path] = set()
    while pending:
        root = pending.pop().resolve()
        if root in visited:
            continue
        visited.add(root)
        result = _run_git(root, [
            "config", "--null", "--name-only", "--get-regexp", r"^filter\..*\.(clean|process|required)$",
        ], deadline)
        if result.returncode not in {0, 1}:
            raise RuntimeError(f"cannot inspect Git filter configuration: {result.stderr}")
        drivers.update(key.rsplit(".", 1)[0] for key in result.stdout.split("\0") if key)
        index = _git_stdout(root, ["ls-files", "--stage", "-z"], deadline)
        for entry in index.split("\0"):
            if entry.startswith("160000 ") and "\t" in entry:
                child = root / entry.split("\t", 1)[1]
                if (child / ".git").exists():
                    pending.append(child)
    return drivers


def _start_git(args: list[str], *, deadline: float | None = None, **kwargs: Any) -> subprocess.Popen[Any]:
    """Start git inside the caller's supervision.

    POSIX keeps git in the caller's process group, so cancelling the wrapper
    also stops it. Windows gives git a job nested in the caller's job, so a
    timeout can stop the Git helpers that share its pipes, and terminating the
    wrapper's job still stops them.
    """

    overrides: list[str] = []
    if args and args[0] in {"status", "diff"}:
        drivers = _git_filter_drivers(Path(kwargs["cwd"]), _snapshot_deadline(deadline))
        for driver in sorted(drivers):
            overrides.extend(["-c", f"{driver}.clean=", "-c", f"{driver}.process=",
                              "-c", f"{driver}.required=false"])
    command = [*GIT_BASE_COMMAND, *overrides, *args]
    if deadline is not None:
        _remaining_seconds(deadline)
    if os.name == "nt":
        return SAFETY.popen_group(command, **kwargs)
    return subprocess.Popen(command, **kwargs)


def _stop_git(process: subprocess.Popen[Any]) -> None:
    if os.name == "nt":
        SAFETY.terminate_process_group(process.pid, term_timeout_seconds=0, kill_timeout_seconds=2)
    else:
        process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _run_git(repo_root: Path, args: list[str], deadline: float) -> subprocess.CompletedProcess[str]:
    try:
        timeout = _remaining_seconds(deadline)
        process = _start_git(
            args,
            deadline=deadline,
            cwd=repo_root,
            env=_git_env(),
            encoding="utf-8",
            errors="surrogateescape",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        timeout = max(0.0, deadline - time.monotonic())
    except TimeoutError:
        raise
    except OSError as exc:
        raise RuntimeError(f"could not execute git {' '.join(args)}: {exc}") from exc
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _stop_git(process)
        if os.name == "nt":
            # communicate()'s reader threads finish once git's job is gone.
            try:
                process.communicate(timeout=1)
            except (subprocess.TimeoutExpired, OSError, ValueError):
                pass
        else:
            # Like subprocess.run, do not drain pipes a surviving helper may
            # hold open.
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        raise TimeoutError(f"git {' '.join(args)} exceeded the workspace snapshot deadline") from exc
    except BaseException:
        _stop_git(process)
        raise
    return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)


def has_git_metadata(path: Path) -> bool:
    """Return whether path or one of its parents holds Git metadata."""

    return any((candidate / ".git").exists() for candidate in (path, *path.parents))


def is_git_workspace(repo_root: Path, deadline: float) -> bool:
    if shutil.which("git", path=_git_env().get("PATH")) is None:
        # Git is needed only for Git repositories; any other folder uses a
        # filesystem snapshot.
        if has_git_metadata(repo_root):
            raise RuntimeError("git is required to snapshot a Git repository, but it is not on PATH")
        return False
    completed = _run_git(repo_root, ["rev-parse", "--is-inside-work-tree"], deadline)
    if completed.returncode == 0:
        return completed.stdout.strip() == "true"
    # A directory containing Git metadata must never silently fall back to a
    # weaker filesystem snapshot when Git itself is unhealthy.
    if (repo_root / ".git").exists():
        detail = completed.stderr.strip() or f"exit status {completed.returncode}"
        raise RuntimeError(f"git workspace detection failed: {detail}")
    return False


def _git_stdout(repo_root: Path, args: list[str], deadline: float) -> str:
    completed = _run_git(repo_root, args, deadline)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit status {completed.returncode}"
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return completed.stdout.rstrip("\n")


def _git_output_digest(repo_root: Path, args: list[str], deadline: float) -> str:
    """Hash potentially large Git output without retaining a patch or index listing."""
    try:
        with tempfile.TemporaryFile(mode="w+b") as errors:
            process = _start_git(args, deadline=deadline, cwd=repo_root, env=_git_env(), stdout=subprocess.PIPE, stderr=errors)
            assert process.stdout is not None
            stdout = process.stdout
            digest = hashlib.sha256()
            size = 0
            read_errors: list[BaseException] = []
            reader: threading.Thread | None = None

            def drain() -> None:
                nonlocal size
                try:
                    while chunk := stdout.read1(1024 * 1024):
                        digest.update(chunk)
                        size += len(chunk)
                except BaseException as exc:
                    read_errors.append(exc)

            try:
                if os.name == "nt":
                    # select() accepts only sockets on Windows, so read on a
                    # thread; stopping git's job ends the read.
                    thread = threading.Thread(target=drain, daemon=True)
                    thread.start()
                    reader = thread
                    reader.join(_remaining_seconds(deadline))
                    if reader.is_alive():
                        raise TimeoutError(f"git {' '.join(args)} exceeded the workspace snapshot deadline")
                    if read_errors:
                        raise read_errors[0]
                else:
                    while True:
                        ready, _, _ = select.select([stdout], [], [], _remaining_seconds(deadline))
                        if not ready:
                            raise TimeoutError(f"git {' '.join(args)} exceeded the workspace snapshot deadline")
                        chunk = stdout.read1(1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                        size += len(chunk)
                returncode = process.wait(timeout=_remaining_seconds(deadline))
            except (TimeoutError, subprocess.TimeoutExpired):
                _stop_git(process)
                raise TimeoutError(f"git {' '.join(args)} exceeded the workspace snapshot deadline")
            except BaseException:
                if process.poll() is None:
                    _stop_git(process)
                raise
            finally:
                # A blocked read holds the reader's lock; close only after the
                # stopped job has let it finish.
                if reader is not None:
                    reader.join(1)
                if reader is None or not reader.is_alive():
                    stdout.close()
            if returncode != 0:
                errors.seek(0)
                detail = errors.read(8192).decode("utf-8", "replace").strip()
                raise RuntimeError(f"git {' '.join(args)} failed: {detail or f'exit status {returncode}'}")
            return f"sha256={digest.hexdigest()} bytes={size}"
    except TimeoutError:
        raise
    except OSError as exc:
        raise RuntimeError(f"could not execute git {' '.join(args)}: {exc}") from exc


def _git_head(repo_root: Path, deadline: float) -> str:
    completed = _run_git(repo_root, ["rev-parse", "--verify", "HEAD^{commit}"], deadline)
    if completed.returncode == 0:
        return completed.stdout.strip()
    symbolic = _run_git(repo_root, ["symbolic-ref", "-q", "HEAD"], deadline)
    if symbolic.returncode == 0 and symbolic.stdout.strip():
        return f"<unborn:{symbolic.stdout.strip()}>"
    detail = completed.stderr.strip() or f"exit status {completed.returncode}"
    raise RuntimeError(f"git rev-parse --verify HEAD^{{commit}} failed: {detail}")


def _git_pathspecs(repo_root: Path, ignored_paths: Iterable[Path] | None = None) -> list[str]:
    repo_root = _resolve(repo_root)
    pathspecs = ["--", "."]
    for ignored in _normalize_ignored_paths(ignored_paths):
        if not ignored.is_relative_to(repo_root):
            continue
        rel = ignored.relative_to(repo_root).as_posix()
        if rel:
            pathspecs.append(f":(exclude){rel}")
            pathspecs.append(f":(exclude){rel}/**")
    return pathspecs


def _digest_lines(lines: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for line in lines:
        digest.update(line.encode("utf-8", "surrogateescape"))
        digest.update(b"\0")
    return digest.hexdigest()


def _bounded_entries(entries: dict[str, str]) -> tuple[dict[str, str], int, str]:
    """Retain useful paths while binding every entry into a full digest."""
    retained: dict[str, str] = {}
    retained_bytes = 0
    digest = hashlib.sha256()
    for path, signature in sorted(entries.items()):
        line = f"{path}\0{signature}".encode("utf-8", "surrogateescape")
        digest.update(line)
        digest.update(b"\0")
        if len(retained) < MAX_RETAINED_ENTRIES and retained_bytes + len(line) <= MAX_RETAINED_ENTRY_BYTES:
            retained[_printable(path)] = _printable(signature)
            retained_bytes += len(line)
    return retained, len(entries), digest.hexdigest()


def _bounded_status(value: str, separator: str) -> tuple[str, str, bool]:
    raw = value.encode("utf-8", "surrogateescape")
    digest = hashlib.sha256(raw).hexdigest()
    if len(raw) <= MAX_RETAINED_STATUS_BYTES:
        return _printable(value), digest, False
    boundary = raw.rfind(separator.encode(), 0, MAX_RETAINED_STATUS_BYTES)
    prefix = raw[:boundary + 1] if boundary >= 0 else b""
    return _printable(prefix.decode("utf-8", "surrogateescape")), digest, True


def _hash_file(path: Path, deadline: float) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            _remaining_seconds(deadline)
            digest.update(chunk)
    return digest.hexdigest()


def _path_signature(path: Path, deadline: float) -> str:
    _remaining_seconds(deadline)
    try:
        if path.is_symlink():
            return f"symlink target={os.readlink(path)!r}"
        if path.is_file():
            stat = path.stat(follow_symlinks=False)
            return f"file size={stat.st_size} mode={stat.st_mode & 0o777:o} sha256={_hash_file(path, deadline)}"
        if path.is_dir():
            stat = path.stat(follow_symlinks=False)
            return f"dir mode={stat.st_mode & 0o777:o}"
        stat = path.stat(follow_symlinks=False)
        return f"other mode={stat.st_mode & 0o777:o}"
    except TimeoutError:
        raise
    except OSError as exc:
        raise RuntimeError(f"could not snapshot {path}: {exc}") from exc


def _git_untracked_entries(
    repo_root: Path,
    ignored_paths: Iterable[Path] | None,
    deadline: float,
) -> dict[str, str]:
    repo_root = _resolve(repo_root)
    ignored = _normalize_ignored_paths(ignored_paths)
    output = _git_stdout(
        repo_root,
        ["ls-files", "--others", "--exclude-standard", "-z", *_git_pathspecs(repo_root, ignored_paths)],
        deadline,
    )
    entries: dict[str, str] = {}
    for raw_path in (item for item in output.split("\0") if item):
        path = repo_root / raw_path
        if should_ignore(path, repo_root, ignored):
            continue
        entries[raw_path] = _path_signature(path, deadline)
    return dict(sorted(entries.items()))


def _git_ignored_pathspecs(repo_root: Path, ignored_paths: Iterable[Path] | None = None) -> list[str]:
    pathspecs = _git_pathspecs(repo_root, ignored_paths)
    for dirname in sorted(IGNORED_DIR_NAMES):
        pathspecs.extend(
            [
                f":(glob,exclude){dirname}/**",
                f":(glob,exclude)**/{dirname}/**",
            ]
        )
    for prefix in sorted(IGNORED_REL_PREFIXES):
        pathspecs.append(f":(glob,exclude){'/'.join(prefix)}/**")
    for relative_file in sorted(IGNORED_REL_FILES):
        pathspecs.append(f":(exclude){'/'.join(relative_file)}")
    return pathspecs


def _git_ignored_entries(
    repo_root: Path,
    ignored_paths: Iterable[Path] | None,
    deadline: float,
) -> dict[str, str]:
    """Hash ignored files except known high-volume generated directories."""

    repo_root = _resolve(repo_root)
    ignored = _normalize_ignored_paths(ignored_paths)
    output = _git_stdout(
        repo_root,
        [
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
            "-z",
            *_git_ignored_pathspecs(repo_root, ignored_paths),
        ],
        deadline,
    )
    entries: dict[str, str] = {}
    for raw_path in (item for item in output.split("\0") if item):
        path = repo_root / raw_path
        if should_ignore(path, repo_root, ignored):
            continue
        entries[raw_path] = _path_signature(path, deadline)
    return dict(sorted(entries.items()))


def _git_control_entries(repo_root: Path, deadline: float) -> dict[str, str]:
    """Hash mutable Git configuration and executable hook surfaces."""

    git_dir = Path(
        _git_stdout(repo_root, ["rev-parse", "--path-format=absolute", "--git-dir"], deadline)
    )
    common_dir = Path(
        _git_stdout(
            repo_root,
            ["rev-parse", "--path-format=absolute", "--git-common-dir"],
            deadline,
        )
    )
    candidates: list[tuple[str, Path]] = [
        ("common/config", common_dir / "config"),
        ("common/info/exclude", common_dir / "info" / "exclude"),
        ("common/info/attributes", common_dir / "info" / "attributes"),
        ("common/packed-refs", common_dir / "packed-refs"),
        ("worktree/HEAD", git_dir / "HEAD"),
        # The raw index is omitted: read-only status refreshes rewrite its stat
        # cache. Staged content and assume-unchanged/skip-worktree flags are
        # covered by the `ls-files --stage -v` index digest.
        ("worktree/config.worktree", git_dir / "config.worktree"),
    ]
    refs_dir = common_dir / "refs"
    if refs_dir.is_dir():
        for ref in sorted(refs_dir.rglob("*")):
            _remaining_seconds(deadline)
            if ref.is_file() or ref.is_symlink():
                candidates.append((f"common/refs/{ref.relative_to(refs_dir).as_posix()}", ref))
    hooks_dir = common_dir / "hooks"
    if hooks_dir.is_dir():
        for hook in sorted(hooks_dir.rglob("*")):
            _remaining_seconds(deadline)
            if hook.is_file() or hook.is_symlink():
                candidates.append((f"common/hooks/{hook.relative_to(hooks_dir).as_posix()}", hook))
    entries: dict[str, str] = {}
    for label, path in candidates:
        if path.exists() or path.is_symlink():
            entries[label] = _path_signature(path, deadline)
    return dict(sorted(entries.items()))


def _git_tracked_entries(
    repo_root: Path,
    ignored_paths: Iterable[Path] | None,
    deadline: float,
) -> dict[str, str]:
    """Record working-tree metadata Git status cannot see.

    Every tracked path contributes its type and permission bits, which catches
    mode-only changes Git does not track. Paths Git deliberately skips
    (assume-unchanged or skip-worktree) also contribute a content hash.
    """

    repo_root = _resolve(repo_root)
    output = _git_stdout(repo_root, ["ls-files", "-v", "-z", *_git_pathspecs(repo_root, ignored_paths)], deadline)
    entries: dict[str, str] = {}
    for entry in (item for item in output.split("\0") if item):
        tag, _, raw_path = entry.partition(" ")
        path = repo_root / raw_path
        _remaining_seconds(deadline)
        try:
            stat = path.lstat()
        except FileNotFoundError:
            entries[raw_path] = f"missing tag={tag}"
            continue
        except OSError as exc:
            entries[raw_path] = f"error error={type(exc).__name__}"
            continue
        signature = f"mode={stat.st_mode:o}"
        if tag.islower() or tag == "S":
            signature = f"{_path_signature(path, deadline)} tag={tag}"
        entries[raw_path] = signature
    return dict(sorted(entries.items()))


def _filesystem_entries(
    repo_root: Path,
    ignored_paths: Iterable[Path] | None,
    deadline: float,
) -> dict[str, str]:
    repo_root = _resolve(repo_root)
    ignored = _normalize_ignored_paths(ignored_paths)
    entries: dict[str, str] = {}
    stack = [repo_root]
    while stack:
        _remaining_seconds(deadline)
        current = stack.pop()
        try:
            children = sorted(os.scandir(current), key=lambda entry: entry.name)
        except TimeoutError:
            raise
        except OSError as exc:
            if current != repo_root:
                rel = current.relative_to(repo_root).as_posix()
                entries[rel] = f"error error={type(exc).__name__}"
            continue
        for child in children:
            _remaining_seconds(deadline)
            path = Path(child.path)
            if should_ignore(path, repo_root, ignored):
                continue
            rel = _absolute(path).relative_to(repo_root).as_posix()
            _remaining_seconds(deadline)
            try:
                if child.is_symlink():
                    entries[rel] = f"symlink target={os.readlink(path)!r}"
                elif child.is_dir(follow_symlinks=False):
                    stat = child.stat(follow_symlinks=False)
                    entries[rel] = f"dir mode={stat.st_mode & 0o777:o}"
                    stack.append(path)
                elif child.is_file(follow_symlinks=False):
                    stat = child.stat(follow_symlinks=False)
                    entries[rel] = (
                        f"file size={stat.st_size} mode={stat.st_mode & 0o777:o} "
                        f"sha256={_hash_file(path, deadline)}"
                    )
                else:
                    stat = child.stat(follow_symlinks=False)
                    entries[rel] = f"other mode={stat.st_mode & 0o777:o}"
            except TimeoutError:
                # TimeoutError is an OSError; an expired deadline is not a file error.
                raise
            except OSError as exc:
                entries[rel] = f"error error={type(exc).__name__}"
    return dict(sorted(entries.items()))


# Raw bytes, no textconv rewriting, and submodule content changes inline.
GIT_DIFF_OPTIONS = ("--binary", "--no-ext-diff", "--no-textconv", "--submodule=diff")


def _git_snapshot(
    repo_root: Path,
    ignored_paths: Iterable[Path] | None,
    deadline: float,
) -> dict[str, Any]:
    pathspecs = _git_pathspecs(repo_root, ignored_paths)
    head = _git_head(repo_root, deadline)
    index = _git_output_digest(repo_root, ["ls-files", "--stage", "-v", "-z", *pathspecs], deadline)
    tracked, tracked_count, tracked_digest = _bounded_entries(
        _git_tracked_entries(repo_root, ignored_paths, deadline)
    )
    untracked, untracked_count, untracked_digest = _bounded_entries(
        _git_untracked_entries(repo_root, ignored_paths, deadline)
    )
    ignored, ignored_count, ignored_digest = _bounded_entries(
        _git_ignored_entries(repo_root, ignored_paths, deadline)
    )
    git_control, git_control_count, git_control_digest = _bounded_entries(
        _git_control_entries(repo_root, deadline)
    )
    status, status_digest, status_truncated = _bounded_status(
        _git_stdout(repo_root, ["status", "--porcelain=v1", "--untracked-files=no", *pathspecs], deadline),
        "\n",
    )
    status_z, status_z_digest, status_z_truncated = _bounded_status(
        _git_stdout(repo_root, ["status", "--porcelain=v1", "-z", "--untracked-files=no", *pathspecs], deadline),
        "\0",
    )
    sections = {
        "head": head,
        "index": index,
        "tracked": tracked,
        "tracked_count": tracked_count,
        "tracked_digest": tracked_digest,
        "untracked": untracked,
        "untracked_count": untracked_count,
        "untracked_digest": untracked_digest,
        "ignored": ignored,
        "ignored_count": ignored_count,
        "ignored_digest": ignored_digest,
        "git_control": git_control,
        "git_control_count": git_control_count,
        "git_control_digest": git_control_digest,
        # Track working-tree changes here, but hash untracked files through the
        # separately filtered manifest so generated-directory exclusions do
        # not hide tracked files with the same path prefix.
        "status": status,
        "status_digest": status_digest,
        "status_truncated": status_truncated,
        "status_z": status_z,
        "status_z_digest": status_z_digest,
        "status_z_truncated": status_z_truncated,
        "diff": _git_output_digest(repo_root, ["diff", *GIT_DIFF_OPTIONS, *pathspecs], deadline),
        "diff_cached": _git_output_digest(repo_root, ["diff", "--cached", *GIT_DIFF_OPTIONS, *pathspecs], deadline),
    }
    digest = _digest_lines(
        [
            head,
            index,
            status_z_digest,
            sections["diff"],
            sections["diff_cached"],
            tracked_digest,
            untracked_digest,
            ignored_digest,
            git_control_digest,
        ]
    )
    return {
        "schema_version": "2.0",
        "mode": "git",
        "repo_root": str(_resolve(repo_root)),
        "digest": digest,
        "sections": sections,
    }


def _filesystem_snapshot(
    repo_root: Path,
    ignored_paths: Iterable[Path] | None,
    deadline: float,
) -> dict[str, Any]:
    entries, count, digest = _bounded_entries(_filesystem_entries(repo_root, ignored_paths, deadline))
    return {
        "schema_version": "2.0",
        "mode": "filesystem",
        "repo_root": str(_resolve(repo_root)),
        "digest": digest,
        "entries": entries,
        "entries_count": count,
    }


def mutation_snapshot(
    repo_root: Path,
    ignored_paths: Iterable[Path] | None = None,
    *,
    deadline: float | None = None,
) -> dict[str, Any]:
    repo_root = _resolve(repo_root)
    effective_deadline = _snapshot_deadline(deadline)
    if is_git_workspace(repo_root, effective_deadline):
        return _git_snapshot(repo_root, ignored_paths, effective_deadline)
    return _filesystem_snapshot(repo_root, ignored_paths, effective_deadline)


def _git_changed_paths(snapshot: dict[str, Any]) -> list[str]:
    status_z = snapshot.get("sections", {}).get("status_z", "")
    if isinstance(status_z, str) and status_z:
        entries = [entry for entry in status_z.split("\0") if entry]
        paths: list[str] = []
        index = 0
        while index < len(entries):
            entry = entries[index]
            status = entry[:2]
            path = entry[3:] if len(entry) > 3 and entry[2] == " " else entry
            if path:
                paths.append(path)
            if ("R" in status or "C" in status) and index + 1 < len(entries):
                index += 1
                if entries[index]:
                    paths.append(entries[index])
            index += 1
        return sorted(path for path in paths if path)

    status = snapshot.get("sections", {}).get("status", "")
    paths: list[str] = []
    for line in status.splitlines():
        if not line:
            continue
        text = line[3:] if len(line) > 3 else line
        if " -> " in text:
            paths.extend(part.strip() for part in text.split(" -> ", 1))
        else:
            paths.append(text.strip())
    return sorted(path for path in paths if path)


def diff_snapshots(before: dict[str, Any], after: dict[str, Any], limit: int = 200) -> dict[str, Any]:
    changed = before != after
    mode = str(after.get("mode") or before.get("mode") or "unknown")
    changed_paths: list[str] = []
    if before.get("mode") == "filesystem" and after.get("mode") == "filesystem":
        before_entries = before.get("entries", {})
        after_entries = after.get("entries", {})
        if isinstance(before_entries, dict) and isinstance(after_entries, dict):
            keys = set(before_entries) | set(after_entries)
            changed_paths = sorted(path for path in keys if before_entries.get(path) != after_entries.get(path))
        if (before.get("digest") != after.get("digest")
                and (before.get("entries_count", len(before_entries)) > len(before_entries)
                     or after.get("entries_count", len(after_entries)) > len(after_entries))):
            changed_paths.append("<filesystem entries beyond retained path sample may have changed>")
    elif before.get("mode") == "git" and after.get("mode") == "git":
        changed_path_set: set[str] = set()
        before_sections = before.get("sections", {})
        after_sections = after.get("sections", {})
        if isinstance(before_sections, dict) and isinstance(after_sections, dict):
            if any(before_sections.get(key) != after_sections.get(key)
                   for key in ("status_z_digest", "status_digest", "diff", "diff_cached")):
                changed_path_set.update(_git_changed_paths(before))
                changed_path_set.update(_git_changed_paths(after))
                if before_sections.get("status_z_truncated") or after_sections.get("status_z_truncated"):
                    changed_path_set.add("<tracked paths beyond retained status sample may have changed>")
            if before_sections.get("head") != after_sections.get("head"):
                changed_path_set.add("<HEAD>")
            if before_sections.get("index") != after_sections.get("index"):
                changed_path_set.add("<index>")
            before_tracked = before_sections.get("tracked", {})
            after_tracked = after_sections.get("tracked", {})
            if isinstance(before_tracked, dict) and isinstance(after_tracked, dict):
                keys = set(before_tracked) | set(after_tracked)
                changed_path_set.update(
                    path for path in keys if before_tracked.get(path) != after_tracked.get(path)
                )
                if (before_sections.get("tracked_digest") != after_sections.get("tracked_digest")
                        and (before_sections.get("tracked_count", len(before_tracked)) > len(before_tracked)
                             or after_sections.get("tracked_count", len(after_tracked)) > len(after_tracked))):
                    changed_path_set.add("<tracked entries beyond retained path sample may have changed>")
            before_untracked = before_sections.get("untracked", {})
            after_untracked = after_sections.get("untracked", {})
            if isinstance(before_untracked, dict) and isinstance(after_untracked, dict):
                keys = set(before_untracked) | set(after_untracked)
                changed_path_set.update(
                    path for path in keys if before_untracked.get(path) != after_untracked.get(path)
                )
                if (before_sections.get("untracked_digest") != after_sections.get("untracked_digest")
                        and (before_sections.get("untracked_count", len(before_untracked)) > len(before_untracked)
                             or after_sections.get("untracked_count", len(after_untracked)) > len(after_untracked))):
                    changed_path_set.add("<untracked entries beyond retained path sample may have changed>")
            before_ignored = before_sections.get("ignored", {})
            after_ignored = after_sections.get("ignored", {})
            if isinstance(before_ignored, dict) and isinstance(after_ignored, dict):
                keys = set(before_ignored) | set(after_ignored)
                changed_path_set.update(
                    path for path in keys if before_ignored.get(path) != after_ignored.get(path)
                )
                if (before_sections.get("ignored_digest") != after_sections.get("ignored_digest")
                        and (before_sections.get("ignored_count", len(before_ignored)) > len(before_ignored)
                             or after_sections.get("ignored_count", len(after_ignored)) > len(after_ignored))):
                    changed_path_set.add("<ignored entries beyond retained path sample may have changed>")
            before_control = before_sections.get("git_control", {})
            after_control = after_sections.get("git_control", {})
            if isinstance(before_control, dict) and isinstance(after_control, dict):
                keys = set(before_control) | set(after_control)
                changed_path_set.update(
                    f"<git-control>/{path}"
                    for path in keys
                    if before_control.get(path) != after_control.get(path)
                )
                if (before_sections.get("git_control_digest") != after_sections.get("git_control_digest")
                        and (before_sections.get("git_control_count", len(before_control)) > len(before_control)
                             or after_sections.get("git_control_count", len(after_control)) > len(after_control))):
                    changed_path_set.add("<git-control entries beyond retained path sample may have changed>")
        changed_paths = sorted(changed_path_set)
    elif changed:
        changed_paths = ["<snapshot mode changed>"]
    return {
        "changed": changed,
        "snapshot_mode": mode,
        "before_digest": before.get("digest", ""),
        "after_digest": after.get("digest", ""),
        "changed_path_count": len(changed_paths),
        "changed_paths": changed_paths[:limit],
        "changed_paths_truncated": (
            len(changed_paths) > limit
            or any("beyond retained" in path for path in changed_paths)
        ),
    }


def snapshot_text(
    repo_root: Path,
    ignored_paths: Iterable[Path] | None = None,
    *,
    deadline: float | None = None,
    snapshot_data: dict[str, Any] | None = None,
) -> str:
    repo_root = _resolve(repo_root)
    effective_deadline = _snapshot_deadline(deadline)
    snapshot = snapshot_data or mutation_snapshot(
        repo_root,
        ignored_paths,
        deadline=effective_deadline,
    )
    lines = [
        SNAPSHOT_HEADER,
        f"repo={repo_root}",
        f"timestamp_utc={datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"mode={snapshot['mode']}",
        f"digest={snapshot['digest']}",
    ]
    if snapshot["mode"] == "isolated":
        lines.append("Host workspace is outside the disposable peer overlay; no content scan was performed.")
    elif snapshot["mode"] == "git":
        pathspecs = _git_pathspecs(repo_root, ignored_paths)
        status = snapshot["sections"]["status"]
        diff_names, _, diff_names_truncated = _bounded_status(
            _git_stdout(repo_root, ["diff", "--name-status", *pathspecs], effective_deadline), "\n"
        )
        staged_names, _, staged_names_truncated = _bounded_status(
            _git_stdout(repo_root, ["diff", "--cached", "--name-status", *pathspecs], effective_deadline), "\n"
        )
        lines.extend(
            [
                f"branch={_printable(_git_stdout(repo_root, ['branch', '--show-current'], effective_deadline))}",
                f"head={snapshot['sections']['head']}",
                f"dirty={'true' if snapshot['sections']['status_z_digest'] != EMPTY_SHA256 or snapshot['sections']['untracked_count'] else 'false'}",
                f"untracked_entry_count={snapshot['sections']['untracked_count']}",
                f"ignored_entry_count={snapshot['sections']['ignored_count']}",
                f"git_control_entry_count={snapshot['sections']['git_control_count']}",
                f"tracked_entry_count={snapshot['sections']['tracked_count']}",
                f"status_truncated={'true' if snapshot['sections']['status_truncated'] else 'false'}",
                "-- status_porcelain_v1",
                status,
                f"-- diff_name_status{'_sample' if diff_names_truncated else ''}",
                diff_names,
                f"-- staged_name_status{'_sample' if staged_names_truncated else ''}",
                staged_names,
            ]
        )
    else:
        entries = snapshot["entries"]
        lines.extend([f"entry_count={snapshot['entries_count']}", "-- filesystem_manifest_sample"])
        lines.extend(f"{path}\t{signature}" for path, signature in entries.items())
    return "\n".join(lines).rstrip() + "\n"


def write_workspace_snapshot(
    repo_root: Path,
    output_path: Path,
    ignored_paths: Iterable[Path] | None = None,
    *,
    deadline: float | None = None,
    snapshot_data: dict[str, Any] | None = None,
) -> None:
    STRICT_JSON.write_text(
        output_path,
        snapshot_text(
            repo_root,
            ignored_paths,
            deadline=deadline,
            snapshot_data=snapshot_data,
        ),
    )
