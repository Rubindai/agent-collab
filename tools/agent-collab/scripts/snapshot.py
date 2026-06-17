#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SNAPSHOT_HEADER = "agent_collab_workspace_snapshot_v1"
IGNORED_DIR_NAMES = {
    ".agents",
    ".cache",
    ".claude",
    ".codex",
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
    ("codex-plugin", "agent-collab", "skills", "agent-collab", "runs"),
    ("claude-plugin", "agent-collab", "skills", "agent-collab", "runs"),
}
IGNORED_REL_FILES = {
    ("tools", "agent-collab", "settings.local.json"),
    ("codex-plugin", "agent-collab", "skills", "agent-collab", "settings.local.json"),
    ("claude-plugin", "agent-collab", "skills", "agent-collab", "settings.local.json"),
}


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


def _run_git(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError:
        return None


def is_git_workspace(repo_root: Path) -> bool:
    completed = _run_git(repo_root, ["rev-parse", "--is-inside-work-tree"])
    return completed is not None and completed.returncode == 0 and completed.stdout.strip() == "true"


def _git_stdout(repo_root: Path, args: list[str]) -> str:
    completed = _run_git(repo_root, args)
    if completed is None:
        return ""
    return completed.stdout.rstrip("\n")


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


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _filesystem_entries(repo_root: Path, ignored_paths: Iterable[Path] | None = None) -> dict[str, str]:
    repo_root = _resolve(repo_root)
    ignored = _normalize_ignored_paths(ignored_paths)
    entries: dict[str, str] = {}
    stack = [repo_root]
    while stack:
        current = stack.pop()
        try:
            children = sorted(os.scandir(current), key=lambda entry: entry.name)
        except OSError as exc:
            if current != repo_root:
                rel = current.relative_to(repo_root).as_posix()
                entries[rel] = f"error error={type(exc).__name__}"
            continue
        for child in children:
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
                        f"sha256={_hash_file(path)}"
                    )
                else:
                    stat = child.stat(follow_symlinks=False)
                    entries[rel] = f"other mode={stat.st_mode & 0o777:o}"
            except OSError as exc:
                entries[rel] = f"error error={type(exc).__name__}"
    return dict(sorted(entries.items()))


def _git_snapshot(repo_root: Path, ignored_paths: Iterable[Path] | None = None) -> dict[str, Any]:
    pathspecs = _git_pathspecs(repo_root, ignored_paths)
    sections = {
        "status": _git_stdout(repo_root, ["status", "--porcelain=v1", "--untracked-files=all", *pathspecs]),
        "status_z": _git_stdout(repo_root, ["status", "--porcelain=v1", "-z", "--untracked-files=all", *pathspecs]),
        "diff": _git_stdout(repo_root, ["diff", "--binary", "--no-ext-diff", *pathspecs]),
        "diff_cached": _git_stdout(repo_root, ["diff", "--cached", "--binary", "--no-ext-diff", *pathspecs]),
    }
    digest = _digest_lines([sections["status_z"], sections["diff"], sections["diff_cached"]])
    return {
        "schema_version": "1.0",
        "mode": "git",
        "repo_root": str(_resolve(repo_root)),
        "digest": digest,
        "sections": sections,
    }


def _filesystem_snapshot(repo_root: Path, ignored_paths: Iterable[Path] | None = None) -> dict[str, Any]:
    entries = _filesystem_entries(repo_root, ignored_paths)
    digest = _digest_lines(f"{path}\0{signature}" for path, signature in entries.items())
    return {
        "schema_version": "1.0",
        "mode": "filesystem",
        "repo_root": str(_resolve(repo_root)),
        "digest": digest,
        "entries": entries,
    }


def mutation_snapshot(repo_root: Path, ignored_paths: Iterable[Path] | None = None) -> dict[str, Any]:
    repo_root = _resolve(repo_root)
    if is_git_workspace(repo_root):
        return _git_snapshot(repo_root, ignored_paths)
    return _filesystem_snapshot(repo_root, ignored_paths)


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
        changed_paths = sorted(set(_git_changed_paths(before)) | set(_git_changed_paths(after)))
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


def snapshot_text(repo_root: Path, ignored_paths: Iterable[Path] | None = None) -> str:
    repo_root = _resolve(repo_root)
    snapshot = mutation_snapshot(repo_root, ignored_paths)
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
                f"branch={_git_stdout(repo_root, ['branch', '--show-current'])}",
                f"head={_git_stdout(repo_root, ['rev-parse', '--verify', 'HEAD'])}",
                f"dirty={'true' if status else 'false'}",
                "-- status_porcelain_v1",
                status,
                "-- diff_name_status",
                _git_stdout(repo_root, ["diff", "--name-status", *pathspecs]),
                "-- staged_name_status",
                _git_stdout(repo_root, ["diff", "--cached", "--name-status", *pathspecs]),
            ]
        )
    else:
        entries = snapshot["entries"]
        lines.extend([f"entry_count={len(entries)}", "-- filesystem_manifest"])
        lines.extend(f"{path}\t{signature}" for path, signature in entries.items())
    return "\n".join(lines).rstrip() + "\n"


def write_workspace_snapshot(repo_root: Path, output_path: Path, ignored_paths: Iterable[Path] | None = None) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(snapshot_text(repo_root, ignored_paths), encoding="utf-8")
