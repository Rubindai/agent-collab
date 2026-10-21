#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import os
import stat
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, TextIO


class StrictJSONError(ValueError):
    pass


PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
# Windows opens descriptors in text mode unless asked otherwise, which would
# rewrite LF as CRLF and stop reads at a Ctrl-Z byte.
BINARY_FLAG = getattr(os, "O_BINARY", 0)
REPLACE_RETRY_SECONDS = 2.0


def ensure_private_directory(path: Path) -> Path:
    """Create a runtime directory and force an owner-only POSIX mode."""

    path.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIRECTORY_MODE)
    if os.name == "posix":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                             | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
        try:
            os.fchmod(descriptor, PRIVATE_DIRECTORY_MODE)
        finally:
            os.close(descriptor)
    return path


def reject_symlink_destination(path: Path) -> None:
    """Reject existing final-component links without resolving their targets."""
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if stat.S_ISLNK(mode):
        raise OSError(f"artifact destination must not be a symlink: {path}")


def open_private_text(path: Path) -> TextIO:
    """Open a private text artifact without following a final-component symlink."""

    ensure_private_directory(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | BINARY_FLAG
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, PRIVATE_FILE_MODE)
    try:
        if os.name == "posix":
            os.fchmod(descriptor, PRIVATE_FILE_MODE)
        return os.fdopen(descriptor, "w", encoding="utf-8", newline="")
    except BaseException:
        os.close(descriptor)
        raise


def write_text(path: Path, text: str) -> None:
    """Atomically publish owner-only UTF-8 text."""

    if not isinstance(text, str):
        raise TypeError("text artifact must be a string")
    ensure_private_directory(path.parent)
    reject_symlink_destination(path)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        if os.name == "posix":
            os.fchmod(descriptor, PRIVATE_FILE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            descriptor = -1
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        reject_symlink_destination(path)
        replace(temporary_path, path)
        fsync_directory(path.parent)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)
        raise


def use_utf8_stdio() -> None:
    """Use UTF-8 and LF on standard streams.

    Windows otherwise uses the ANSI code page and CRLF for redirected
    streams, which corrupts JSON reports and rejects valid hook input.
    """

    if os.name != "nt":
        return
    for stream, errors in ((sys.stdin, "strict"), (sys.stdout, "strict"), (sys.stderr, "backslashreplace")):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors=errors, newline="\n")


def replace(source: Path, destination: Path) -> None:
    """Atomically replace destination, retrying Windows sharing violations.

    Windows refuses to replace a file that another process (a concurrent
    reader or an antivirus scanner) holds open; such holds are brief.
    """

    deadline = time.monotonic() + REPLACE_RETRY_SECONDS
    while True:
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if os.name != "nt" or time.monotonic() >= deadline:
                raise
            time.sleep(0.02)


def open_lock_file(path: Path) -> int:
    """Open an owner-only lock file without following a final symlink."""

    flags = os.O_RDWR | os.O_CREAT | BINARY_FLAG
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, PRIVATE_FILE_MODE)
    if os.name == "posix":
        try:
            os.fchmod(descriptor, PRIVATE_FILE_MODE)
        except BaseException:
            os.close(descriptor)
            raise
    return descriptor


def try_lock(descriptor: int) -> bool:
    """Take an exclusive lock without waiting; return False when another holder has it."""

    if os.name == "nt":
        import msvcrt

        # Lock one byte at offset 0; Windows allows a lock past end of file.
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def unlock(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


def fsync_directory(path: Path) -> None:
    try:
        directory_fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _object_from_pairs(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise StrictJSONError(f"duplicate JSON object key: {key!r}")
        value[key] = item
    return value


def _reject_constant(token: str) -> None:
    raise StrictJSONError(f"non-finite JSON number is unsupported: {token}")


def _parse_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise StrictJSONError(f"non-finite JSON number is unsupported: {token}")
    return value


def loads(text: str, *, max_bytes: int | None = None) -> Any:
    if not isinstance(text, str):
        raise StrictJSONError("JSON input must be text")
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise StrictJSONError(f"JSON input is not valid Unicode text: {exc}") from exc
    if max_bytes is not None and size > max_bytes:
        raise StrictJSONError(f"JSON input is {size} bytes; limit is {max_bytes}")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_from_pairs,
            parse_constant=_reject_constant,
            parse_float=_parse_float,
        )
    except StrictJSONError:
        raise
    except (RecursionError, ValueError) as exc:
        # ValueError also covers integers beyond Python's digit limit.
        raise StrictJSONError(str(exc)) from exc
    _require_unicode_scalars(value)
    return value


def _require_unicode_scalars(value: Any) -> None:
    """Reject lone surrogates (for example "\\ud800") that cannot be re-encoded."""

    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise StrictJSONError("JSON string contains a lone surrogate code point") from exc
        elif isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)


def read_text(path: Path, *, max_bytes: int | None = None) -> str:
    """Read UTF-8 text with an optional pre-allocation byte bound."""

    try:
        if max_bytes is None:
            return path.read_text(encoding="utf-8")
        else:
            if type(max_bytes) is not int or max_bytes < 0:
                raise StrictJSONError("max_bytes must be a non-negative integer")
            with path.open("rb") as handle:
                payload = handle.read(max_bytes + 1)
            if len(payload) > max_bytes:
                raise StrictJSONError(
                    f"JSON file is larger than the {max_bytes}-byte limit"
                )
            return payload.decode("utf-8")
    except UnicodeError as exc:
        raise StrictJSONError(f"{path} is not valid UTF-8: {exc}") from exc


def load(path: Path, *, max_bytes: int | None = None) -> Any:
    return loads(read_text(path, max_bytes=max_bytes))


def dumps(value: Any, *, indent: int | None = 2, sort_keys: bool = True) -> str:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=indent,
            sort_keys=sort_keys,
        )
        text.encode("utf-8")
        return text
    except (RecursionError, TypeError, ValueError) as exc:
        raise StrictJSONError(f"value is not strict JSON: {exc}") from exc


def write(path: Path, value: Any) -> None:
    write_text(path, dumps(value) + "\n")
