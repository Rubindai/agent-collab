#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable


class StrictJSONError(ValueError):
    pass


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
    size = len(text.encode("utf-8"))
    if max_bytes is not None and size > max_bytes:
        raise StrictJSONError(f"JSON input is {size} bytes; limit is {max_bytes}")
    try:
        return json.loads(
            text,
            object_pairs_hook=_object_from_pairs,
            parse_constant=_reject_constant,
            parse_float=_parse_float,
        )
    except (RecursionError, json.JSONDecodeError) as exc:
        raise StrictJSONError(str(exc)) from exc


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
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=indent,
            sort_keys=sort_keys,
        )
    except (RecursionError, TypeError, ValueError) as exc:
        raise StrictJSONError(f"value is not strict JSON: {exc}") from exc


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dumps(value) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
