#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
import subprocess
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


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


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
        if _resolve(path) == ignored or _is_relative_to(_resolve(path), ignored):
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


def _run_git(repo_root: Path, args: list[str], deadline: float) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=_remaining_seconds(deadline),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"git {' '.join(args)} exceeded the workspace snapshot deadline") from exc
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"could not execute git {' '.join(args)}: {exc}") from exc


def is_git_workspace(repo_root: Path, deadline: float) -> bool:
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
        if not _is_relative_to(ignored, repo_root):
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
        ("common/packed-refs", common_dir / "packed-refs"),
        ("worktree/HEAD", git_dir / "HEAD"),
        ("worktree/index", git_dir / "index"),
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
            except OSError as exc:
                entries[rel] = f"error error={type(exc).__name__}"
    return dict(sorted(entries.items()))


def _git_snapshot(
    repo_root: Path,
    ignored_paths: Iterable[Path] | None,
    deadline: float,
) -> dict[str, Any]:
    pathspecs = _git_pathspecs(repo_root, ignored_paths)
    head = _git_head(repo_root, deadline)
    index = _git_stdout(repo_root, ["ls-files", "--stage", "-z", *pathspecs], deadline)
    untracked = _git_untracked_entries(repo_root, ignored_paths, deadline)
    ignored = _git_ignored_entries(repo_root, ignored_paths, deadline)
    git_control = _git_control_entries(repo_root, deadline)
    sections = {
        "head": head,
        "index": index,
        "untracked": untracked,
        "ignored": ignored,
        "git_control": git_control,
        "status": _git_stdout(repo_root, ["status", "--porcelain=v1", "--untracked-files=all", *pathspecs], deadline),
        "status_z": _git_stdout(repo_root, ["status", "--porcelain=v1", "-z", "--untracked-files=all", *pathspecs], deadline),
        "diff": _git_stdout(repo_root, ["diff", "--binary", "--no-ext-diff", *pathspecs], deadline),
        "diff_cached": _git_stdout(repo_root, ["diff", "--cached", "--binary", "--no-ext-diff", *pathspecs], deadline),
    }
    untracked_digest = _digest_lines(
        f"{path}\0{signature}" for path, signature in untracked.items()
    )
    ignored_digest = _digest_lines(f"{path}\0{signature}" for path, signature in ignored.items())
    git_control_digest = _digest_lines(
        f"{path}\0{signature}" for path, signature in git_control.items()
    )
    digest = _digest_lines(
        [
            head,
            index,
            sections["status_z"],
            sections["diff"],
            sections["diff_cached"],
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
    entries = _filesystem_entries(repo_root, ignored_paths, deadline)
    digest = _digest_lines(f"{path}\0{signature}" for path, signature in entries.items())
    return {
        "schema_version": "2.0",
        "mode": "filesystem",
        "repo_root": str(_resolve(repo_root)),
        "digest": digest,
        "entries": entries,
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
    elif before.get("mode") == "git" and after.get("mode") == "git":
        changed_path_set = set(_git_changed_paths(before)) | set(_git_changed_paths(after))
        before_sections = before.get("sections", {})
        after_sections = after.get("sections", {})
        if isinstance(before_sections, dict) and isinstance(after_sections, dict):
            if before_sections.get("head") != after_sections.get("head"):
                changed_path_set.add("<HEAD>")
            if before_sections.get("index") != after_sections.get("index"):
                changed_path_set.add("<index>")
            before_untracked = before_sections.get("untracked", {})
            after_untracked = after_sections.get("untracked", {})
            if isinstance(before_untracked, dict) and isinstance(after_untracked, dict):
                keys = set(before_untracked) | set(after_untracked)
                changed_path_set.update(
                    path for path in keys if before_untracked.get(path) != after_untracked.get(path)
                )
            before_ignored = before_sections.get("ignored", {})
            after_ignored = after_sections.get("ignored", {})
            if isinstance(before_ignored, dict) and isinstance(after_ignored, dict):
                keys = set(before_ignored) | set(after_ignored)
                changed_path_set.update(
                    path for path in keys if before_ignored.get(path) != after_ignored.get(path)
                )
            before_control = before_sections.get("git_control", {})
            after_control = after_sections.get("git_control", {})
            if isinstance(before_control, dict) and isinstance(after_control, dict):
                keys = set(before_control) | set(after_control)
                changed_path_set.update(
                    f"<git-control>/{path}"
                    for path in keys
                    if before_control.get(path) != after_control.get(path)
                )
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
        "changed_paths_truncated": len(changed_paths) > limit,
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
    if snapshot["mode"] == "git":
        pathspecs = _git_pathspecs(repo_root, ignored_paths)
        status = snapshot["sections"]["status"]
        lines.extend(
            [
                f"branch={_git_stdout(repo_root, ['branch', '--show-current'], effective_deadline)}",
                f"head={snapshot['sections']['head']}",
                f"dirty={'true' if status else 'false'}",
                f"untracked_entry_count={len(snapshot['sections']['untracked'])}",
                f"ignored_entry_count={len(snapshot['sections']['ignored'])}",
                f"git_control_entry_count={len(snapshot['sections']['git_control'])}",
                "-- status_porcelain_v1",
                status,
                "-- diff_name_status",
                _git_stdout(repo_root, ["diff", "--name-status", *pathspecs], effective_deadline),
                "-- staged_name_status",
                _git_stdout(repo_root, ["diff", "--cached", "--name-status", *pathspecs], effective_deadline),
            ]
        )
    else:
        entries = snapshot["entries"]
        lines.extend([f"entry_count={len(entries)}", "-- filesystem_manifest"])
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
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        snapshot_text(
            repo_root,
            ignored_paths,
            deadline=deadline,
            snapshot_data=snapshot_data,
        ),
        encoding="utf-8",
    )
